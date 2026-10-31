"""Reproducible pilot selection that appends evidence without changing source rows."""

from __future__ import annotations

import csv
import json
import sqlite3
import tempfile
from collections import Counter
from pathlib import Path

import pyarrow as arrow
import pyarrow.parquet as parquet
import pycountry
import yaml

from pipeline.normalize.core import ARROW_SCHEMA as PARENT_SCHEMA
from pipeline.normalize.core import PARSERS, accept_outputs, json_cell, sha256
from pipeline.normalize.core import check as check_normalization
from pipeline.normalize.core import validate_batch as validate_parent_batch
from pipeline.pilot import geology, reporting, scope_review, tabular
from pipeline.pilot.countries import country_evidence
from pipeline.scaffold import digest, json_bytes
from pipeline.vocab.core import Vocabulary

BATCH_SIZE = 10000
ADDED_FIELDS = (
    "pilot_commodities",
    "pilot_families",
    "pilot_evidence",
    "has_qualified_pilot_evidence",
    "country_iso3s",
    "country_evidence",
)
ARROW_SCHEMA = arrow.schema(
    list(PARENT_SCHEMA)
    + [
        arrow.field(
            name,
            arrow.bool_() if name == "has_qualified_pilot_evidence" else arrow.string(),
            nullable=False,
        )
        for name in ADDED_FIELDS
    ],
    metadata={
        b"atlas_schema": b"pilot-source-record-v1",
        b"parent_schema": b"staging-source-record-v1",
    },
)
ROLES = {"primary", "secondary", "tertiary", "byproduct", "unspecified", "collateral"}
BASES = {"source_term", "source_scope", "column_header"}
EXCLUSIONS = {"no_commodity_evidence", "collateral_only_pilot_evidence", "no_pilot_association"}
DIMENSIONS = ("commodity", "family", "host_country", "record_kind")
REVIEW_FIELDS = ("source_id", "field", "raw_value", "unmapped", "count")
SCOPE_FIELDS = (
    "record_id",
    "source_id",
    "raw_name",
    "country",
    "source_file",
    "source_table",
    "source_row",
    "source_url",
    "raw_sha256",
    "raw_commodities",
    "ree_minerals",
    "reason",
    "decision",
    "reviewer",
)
COUNTRY_CODES = frozenset(country.alpha_3 for country in pycountry.countries)
COUNT_REPORTS = frozenset(
    f"reports/pilot-counts-by-{dimension}.csv"
    for dimension in ("source", "commodity", "country", "kind")
)


class PilotError(ValueError):
    """Pilot evidence, source reconciliation or an accepted artifact is invalid."""


def commodity_evidence(source_id: str, row: dict, vocab: Vocabulary) -> list[dict]:
    helper = geology if source_id in geology.SOURCES else tabular
    return helper.commodity_evidence(source_id, row, vocab)


def _strings(value, name: str, *, unique=False) -> list[str]:
    if not isinstance(value, list) or any(not isinstance(v, str) or not v for v in value):
        raise PilotError(f"{name} must be a list of nonempty strings")
    if unique and len(value) != len(set(value)):
        raise PilotError(f"{name} contains duplicates")
    return value


def validate_evidence(evidence, vocab: Vocabulary | None = None) -> None:
    if not isinstance(evidence, list):
        raise PilotError("Commodity evidence must be a list")
    required = {"field", "raw_value", "codes", "role", "basis", "qualified", "unmapped"}
    for item in evidence:
        if (
            not isinstance(item, dict)
            or not required <= item.keys()
            or (item.keys() - required - {"source_path"})
        ):
            raise PilotError("Malformed commodity evidence object")
        for key in ("field", "raw_value"):
            if not isinstance(item[key], str) or not item[key].strip():
                raise PilotError(f"Evidence {key} must be nonempty text")
        codes = _strings(item["codes"], "Commodity codes", unique=True)
        _strings(item["unmapped"], "Unmapped terms")
        if vocab is not None and set(codes) - vocab.entries["commodities"].keys():
            raise PilotError("Evidence contains an unknown commodity code")
        if (
            not isinstance(item["role"], str)
            or item["role"] not in ROLES
            or not isinstance(item["basis"], str)
            or item["basis"] not in BASES
        ):
            raise PilotError("Unrecognized commodity evidence role or basis")
        if type(item["qualified"]) is not bool:
            raise PilotError("Evidence qualified flag must be boolean")
        if "source_path" in item and (
            not isinstance(item["source_path"], str) or not item["source_path"].startswith("/")
        ):
            raise PilotError("Evidence source_path must be a JSON pointer")


def validate_country(result) -> None:
    if not isinstance(result, dict) or set(result) != {"codes", "evidence", "unresolved", "notes"}:
        raise PilotError("Malformed country helper result")
    codes = set(_strings(result["codes"], "Host country codes", unique=True))
    if codes - COUNTRY_CODES:
        raise PilotError("Host country is not an official ISO alpha-3 code")
    _strings(result["unresolved"], "Unresolved countries")
    _strings(result["notes"], "Country notes")
    if not isinstance(result["evidence"], list):
        raise PilotError("Country evidence must be a list")
    hosts, host_sets = set(), set()
    invalid_preferred = False
    for item in result["evidence"]:
        required = {
            "field",
            "location",
            "raw_value",
            "role",
            "codes",
            "unresolved",
            "basis",
            "assigns_country",
            "preferred",
        }
        if not isinstance(item, dict) or not required <= item.keys():
            raise PilotError("Malformed country evidence object")
        for field in ("field", "location", "raw_value", "role", "basis"):
            if not isinstance(item[field], str) or not item[field]:
                raise PilotError("Country evidence must retain textual provenance")
        for field in ("assigns_country", "preferred"):
            if type(item[field]) is not bool:
                raise PilotError("Country evidence flags must be boolean")
        if item["assigns_country"] != (item["role"] == "host_activity"):
            raise PilotError("Only host/activity evidence can assign country")
        item_codes = set(_strings(item["codes"], "Country evidence codes", unique=True))
        if item_codes - COUNTRY_CODES:
            raise PilotError("Country evidence is not an official ISO alpha-3 code")
        _strings(item["unresolved"], "Unresolved country evidence")
        if item["assigns_country"]:
            hosts.update(item_codes)
            if item_codes:
                host_sets.add(frozenset(item_codes))
            invalid_preferred |= item["preferred"] and bool(item["unresolved"])
    expected = set() if len(host_sets) > 1 or invalid_preferred else hosts
    if codes != expected:
        raise PilotError("Assigned countries differ from unambiguous host/activity evidence")


def _pilot_codes(evidence: list[dict], vocab: Vocabulary) -> list[str]:
    return sorted(
        {
            code
            for item in evidence
            if item["role"] != "collateral"
            for code in item["codes"]
            if code in vocab.pilot_codes
        }
    )


def _families(codes: list[str], vocab: Vocabulary) -> list[str]:
    families = set()
    for code in codes:
        if code in {"Ni", "Co"}:
            families.add(code)
        elif vocab.entries["commodities"][code]["group"] in {"rare_earth", "rare_earth_group"}:
            families.add("REE")
        else:
            raise PilotError(f"Pilot code has no reviewed family: {code}")
    return sorted(families)


def _select(source_id: str, row: dict, vocab: Vocabulary, evidence: list[dict]) -> dict | None:
    codes = _pilot_codes(evidence, vocab)
    if not codes:
        return None
    country = country_evidence(source_id, row, vocab)
    validate_country(country)
    return row | {
        "pilot_commodities": json_cell(codes),
        "pilot_families": json_cell(_families(codes, vocab)),
        "pilot_evidence": json_cell(evidence),
        "has_qualified_pilot_evidence": any(
            item["qualified"]
            and item["role"] != "collateral"
            and bool(set(item["codes"]) & set(codes))
            for item in evidence
        ),
        "country_iso3s": json_cell(country["codes"]),
        "country_evidence": json_cell(country),
    }


def select_row(source_id: str, row: dict, vocab: Vocabulary) -> dict | None:
    if row.get("source_id") != source_id:
        raise PilotError("Input record source_id differs from its source artifact")
    evidence = commodity_evidence(source_id, row, vocab)
    validate_evidence(evidence, vocab)
    return _select(source_id, row, vocab, evidence)


def validate_batch(rows: list[dict], vocab: Vocabulary | None = None) -> arrow.Table:
    if not rows:
        return arrow.Table.from_pylist([], schema=ARROW_SCHEMA)
    if any(set(row) != set(ARROW_SCHEMA.names) for row in rows):
        raise PilotError("Pilot row fields differ from the inherited staging schema")
    validate_parent_batch([{key: row[key] for key in PARENT_SCHEMA.names} for row in rows])
    for row in rows:
        if type(row["has_qualified_pilot_evidence"]) is not bool:
            raise PilotError("Qualified pilot evidence flag must be boolean")
        try:
            codes = json.loads(row["pilot_commodities"])
            families = json.loads(row["pilot_families"])
            evidence = json.loads(row["pilot_evidence"])
            country = json.loads(row["country_evidence"])
            countries = json.loads(row["country_iso3s"])
        except (TypeError, ValueError) as error:
            raise PilotError("Malformed pilot JSON field") from error
        _strings(codes, "Pilot commodities", unique=True)
        _strings(families, "Pilot families", unique=True)
        if not codes or not families or set(families) - {"Ni", "Co", "REE"}:
            raise PilotError("Retained row lacks pilot commodities or valid families")
        validate_evidence(evidence, vocab)
        validate_country(country)
        if countries != country["codes"]:
            raise PilotError("Country assignment differs from its evidence")
        if vocab is not None and (
            codes != _pilot_codes(evidence, vocab) or families != _families(codes, vocab)
        ):
            raise PilotError("Pilot associations differ from eligible evidence")
        qualified = any(
            item["qualified"]
            and item["role"] != "collateral"
            and bool(set(item["codes"]) & set(codes))
            for item in evidence
        )
        if qualified != row["has_qualified_pilot_evidence"]:
            raise PilotError("Qualified pilot flag differs from eligible evidence")
    return arrow.Table.from_pylist(rows, schema=ARROW_SCHEMA)


class Review:
    """Disk-backed term counts keep memory bounded even for novel source strings."""

    def __init__(self, path: Path):
        self.db = sqlite3.connect(path)
        self.db.execute(
            "CREATE TABLE terms (source_id TEXT, field TEXT, raw_value TEXT, "
            "unmapped TEXT, count INTEGER, "
            "PRIMARY KEY (source_id, field, raw_value, unmapped))"
        )

    def add(self, source: str, evidence: list[dict]) -> None:
        keys = {
            (source, item["field"], item["raw_value"], json_cell(item["unmapped"]))
            for item in evidence
            if item["unmapped"]
        }
        self.db.executemany(
            "INSERT INTO terms VALUES (?, ?, ?, ?, 1) ON CONFLICT DO UPDATE SET count=count+1",
            sorted(keys),
        )

    def write(self, path: Path) -> dict:
        with path.open("w", newline="", encoding="utf-8") as stream:
            writer = csv.writer(stream, lineterminator="\n")
            writer.writerow(REVIEW_FIELDS)
            writer.writerows(
                self.db.execute(
                    "SELECT * FROM terms ORDER BY source_id, field, raw_value, unmapped"
                )
            )
        distinct, occurrences = self.db.execute(
            "SELECT COUNT(*), COALESCE(SUM(count),0) FROM terms"
        ).fetchone()
        return {
            "distinct_terms": distinct,
            "record_term_occurrences": occurrences,
            "artifact": "review/pilot-unmapped.csv",
            "sha256": sha256(path),
        }

    def close(self):
        self.db.close()


def _counts() -> dict:
    return {name: Counter() for name in DIMENSIONS}


def _count_row(counts: dict, row: dict) -> None:
    for name, field in (
        ("commodity", "pilot_commodities"),
        ("family", "pilot_families"),
        ("host_country", "country_iso3s"),
    ):
        values = json.loads(row[field])
        counts[name].update(set(values) if values else {"UNRESOLVED"})
    counts["record_kind"][row["record_kind"]] += 1


def _sorted_counts(counts: dict) -> dict:
    return {name: dict(sorted(values.items())) for name, values in counts.items()}


def stage_source(root: Path, parent: dict, temp: Path, vocab: Vocabulary, review: Review) -> dict:
    source = parent["source_id"]
    path = root / parent["artifact"]
    file = parquet.ParquetFile(path)
    if not file.schema_arrow.equals(PARENT_SCHEMA):
        raise PilotError("Input schema differs from normalized source records")
    target = temp / f"{source}.parquet"
    inputs = kept = qualified = country_unresolved = 0
    reasons, counts = Counter(), _counts()
    with parquet.ParquetWriter(target, ARROW_SCHEMA, compression="zstd", version="2.6") as writer:
        for batch in file.iter_batches(batch_size=BATCH_SIZE):
            originals = batch.to_pylist()
            validate_parent_batch(originals)
            selected = []
            for row in originals:
                if row["source_id"] != source:
                    raise PilotError("Input record source_id differs from its source artifact")
                inputs += 1
                evidence = commodity_evidence(source, row, vocab)
                validate_evidence(evidence, vocab)
                review.add(source, evidence)
                retained = _select(source, row, vocab, evidence)
                if retained is None:
                    reason = (
                        "no_commodity_evidence"
                        if not evidence
                        else (
                            "collateral_only_pilot_evidence"
                            if any(set(item["codes"]) & vocab.pilot_codes for item in evidence)
                            else "no_pilot_association"
                        )
                    )
                    reasons[reason] += 1
                    continue
                selected.append(retained)
                kept += 1
                qualified += retained["has_qualified_pilot_evidence"]
                country_unresolved += bool(json.loads(retained["country_evidence"])["unresolved"])
                _count_row(counts, retained)
            if selected:
                writer.write_table(validate_batch(selected, vocab), row_group_size=BATCH_SIZE)
    if inputs != parent["rows"] or inputs != kept + sum(reasons.values()):
        raise PilotError(f"Input/kept/excluded counts do not reconcile: {source}")
    return {
        "source_id": source,
        "input_artifact": parent["artifact"],
        "input_sha256": parent["sha256"],
        "input_rows": inputs,
        "kept_rows": kept,
        "excluded_rows": inputs - kept,
        "exclusion_reasons": dict(sorted(reasons.items())),
        "counts": _sorted_counts(counts),
        "qualified_pilot_rows": qualified,
        "unresolved_country_rows": country_unresolved,
        "artifact": f"data/staging/pilot/{source}.parquet",
        "sha256": sha256(target),
    }


def implementation_hashes(root: Path) -> dict:
    paths = [
        *sorted((root / "pipeline/pilot").glob("*.py")),
        *sorted((root / "sources/pilot").glob("*.yaml")),
        root / "uv.lock",
    ]
    return {path.relative_to(root).as_posix(): sha256(path) for path in paths}


def validate_expected_counts(root: Path, results: list[dict]) -> None:
    expected = yaml.safe_load((root / "sources/pilot/expected_counts.yaml").read_text())
    if expected["schema_version"] != "pilot-counts-v1" or set(expected["sources"]) != set(PARSERS):
        raise PilotError("Missing independently reviewed pilot release counts")
    if len(results) != len(PARSERS) or {r["source_id"] for r in results} != set(PARSERS):
        raise PilotError("Duplicate or missing pilot source result")
    for result in results:
        if result["kept_rows"] != expected["sources"][result["source_id"]]:
            raise PilotError(f"Pilot release count mismatch: {result['source_id']}")


def _totals(results: list[dict]) -> dict:
    counts = _counts()
    reasons = Counter()
    for result in results:
        for name in DIMENSIONS:
            counts[name].update(result["counts"][name])
        reasons.update(result["exclusion_reasons"])
    return {
        **{
            field: sum(r[field] for r in results)
            for field in (
                "input_rows",
                "kept_rows",
                "excluded_rows",
                "qualified_pilot_rows",
                "unresolved_country_rows",
            )
        },
        "counts": _sorted_counts(counts),
        "exclusion_reasons": dict(sorted(reasons.items())),
    }


def _write_scope_review(root: Path, temp: Path, vocab: Vocabulary, results: list[dict]) -> dict:
    rows = scope_review.records(root, vocab)
    expected = next((r["excluded_rows"] for r in results if r["source_id"] == "usgs_global_ree"), 0)
    if len(rows) != expected or len({row["record_id"] for row in rows}) != len(rows):
        raise PilotError("REE source-scope review does not reconcile with excluded records")
    if any(
        set(row) != set(SCOPE_FIELDS)
        or row["decision"]
        or row["reviewer"]
        or row["source_id"] != "usgs_global_ree"
        for row in rows
    ):
        raise PilotError("Source-scope review must preserve blank reviewer decisions")
    path = temp / "pilot-scope-review.csv"
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=SCOPE_FIELDS, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
    return {"artifact": "review/pilot-scope-review.csv", "rows": len(rows), "sha256": sha256(path)}


def filter_pilot(root: Path, progress=None) -> dict:
    root = root.resolve()
    parent = check_normalization(root, raw=True)
    vocab = Vocabulary(root)
    incoming = root / "data/.incoming"
    incoming.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="pilot-", dir=incoming) as directory:
        temp = Path(directory)
        review = Review(temp / "review.sqlite")
        try:
            results = []
            for source in sorted(parent["sources"], key=lambda item: item["source_id"]):
                result = stage_source(root, source, temp, vocab, review)
                results.append(result)
                if progress is not None:
                    progress(result)
            validate_expected_counts(root, results)
            review_result = review.write(temp / "pilot-unmapped.csv")
            scope_result = _write_scope_review(root, temp, vocab, results)
        finally:
            review.close()
        count_reports = reporting.generate(results, vocab)
        if set(count_reports) != COUNT_REPORTS:
            raise PilotError("Missing pilot count reports")
        for relative, content in count_reports.items():
            path = temp / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
        report = {
            "schema_version": "pilot-audit-v1",
            "stage": "pilot",
            "phase": 0,
            "step": 5,
            "status": "pass",
            "sources": results,
            **_totals(results),
            "implementation": implementation_hashes(root),
            "normalization_audit_sha256": sha256(root / "reports/normalization-audit.json"),
            "review": review_result,
            "scope_review": scope_result,
            "count_reports": {name: digest(content) for name, content in count_reports.items()},
            "canonical_rows": 0,
            "published_rows": 0,
            "counting_notes": [
                "Each retained source record counts once per distinct commodity, family and "
                "host country. "
                "These categories overlap; their sums are not unique site or mine counts.",
                "Record kinds keep source groups, finance and country/policy context separate "
                "from deposits/facilities.",
                "UNRESOLVED means no assigned host country. Associated financier/owner countries "
                "never assign host geography.",
                "The review queue counts distinct unresolved evidence entries once per input "
                "record, including excluded records.",
                "Collateral-only commodity evidence does not retain a record. Original source "
                "columns are unchanged.",
            ],
        }
        receipt = temp / "pilot-audit.json"
        receipt.write_bytes(json_bytes(report))
        outputs = [(temp / f"{r['source_id']}.parquet", root / r["artifact"]) for r in results]
        outputs.extend((temp / name, root / name) for name in sorted(count_reports))
        outputs.extend(
            [
                (temp / "pilot-unmapped.csv", root / "review/pilot-unmapped.csv"),
                (temp / "pilot-scope-review.csv", root / "review/pilot-scope-review.csv"),
                (receipt, root / "reports/pilot-audit.json"),
            ]
        )
        accept_outputs(outputs)
    return report


def _integer(value, name: str) -> None:
    if type(value) is not int or value < 0:
        raise PilotError(f"{name} must be a nonnegative integer")


def _validate_result(result: dict, parent: dict, vocab: Vocabulary) -> None:
    source = result["source_id"]
    if (result["input_artifact"], result["input_sha256"], result["input_rows"]) != (
        parent["artifact"],
        parent["sha256"],
        parent["rows"],
    ) or result["artifact"] != f"data/staging/pilot/{source}.parquet":
        raise PilotError("Pilot source differs from its normalized parent")
    for name in (
        "input_rows",
        "kept_rows",
        "excluded_rows",
        "qualified_pilot_rows",
        "unresolved_country_rows",
    ):
        _integer(result[name], name)
    kept = result["kept_rows"]
    if result["input_rows"] != kept + result["excluded_rows"]:
        raise PilotError("Pilot source counts do not reconcile")
    if max(result["qualified_pilot_rows"], result["unresolved_country_rows"]) > kept:
        raise PilotError("Pilot row flags exceed retained rows")
    reasons = result["exclusion_reasons"]
    if set(reasons) - EXCLUSIONS or sum(reasons.values()) != result["excluded_rows"]:
        raise PilotError("Pilot exclusion reasons do not reconcile")
    for value in reasons.values():
        _integer(value, "Exclusion count")
    allowed = {
        "commodity": vocab.pilot_codes,
        "family": {"Ni", "Co", "REE"},
        "host_country": COUNTRY_CODES | {"UNRESOLVED"},
        "record_kind": set(parent["kinds"]),
    }
    if set(result["counts"]) != set(DIMENSIONS):
        raise PilotError("Missing pilot counting dimensions")
    for dimension, values in result["counts"].items():
        if set(values) - allowed[dimension]:
            raise PilotError("Unknown pilot count category")
        for value in values.values():
            _integer(value, "Category count")
            if value > kept:
                raise PilotError("Category count exceeds retained records")
        if sum(values.values()) < kept:
            raise PilotError("Category counts omit retained records")
    if sum(result["counts"]["record_kind"].values()) != kept:
        raise PilotError("Record-kind counts do not reconcile")


def check(root: Path, *, raw=False) -> dict:
    parent = check_normalization(root, raw=raw)
    vocab = Vocabulary(root)
    receipt = json.loads((root / "reports/pilot-audit.json").read_text())
    if (
        (
            receipt["schema_version"],
            receipt["phase"],
            receipt["step"],
            receipt["stage"],
            receipt["status"],
        )
        != ("pilot-audit-v1", 0, 5, "pilot", "pass")
        or receipt["canonical_rows"] != 0
        or receipt["published_rows"] != 0
    ):
        raise PilotError("Invalid pilot audit receipt")
    if receipt["implementation"] != implementation_hashes(root):
        raise PilotError("Pilot definitions changed; rebuild the pilot selection")
    if receipt["normalization_audit_sha256"] != sha256(root / "reports/normalization-audit.json"):
        raise PilotError("Normalization receipt changed since pilot selection")
    validate_expected_counts(root, receipt["sources"])
    parents = {result["source_id"]: result for result in parent["sources"]}
    for result in receipt["sources"]:
        _validate_result(result, parents[result["source_id"]], vocab)
        if raw:
            path = root / result["artifact"]
            if sha256(path) != result["sha256"]:
                raise PilotError("Pilot output differs from its accepted receipt")
            file = parquet.ParquetFile(path)
            if file.metadata.num_rows != result["kept_rows"] or not file.schema_arrow.equals(
                ARROW_SCHEMA
            ):
                raise PilotError("Pilot schema or row count changed")
            counts, qualified, unresolved = _counts(), 0, 0
            for batch in file.iter_batches(batch_size=BATCH_SIZE):
                rows = batch.to_pylist()
                validate_batch(rows, vocab)
                for row in rows:
                    if row["source_id"] != result["source_id"]:
                        raise PilotError("Pilot artifact contains another source")
                    _count_row(counts, row)
                    qualified += row["has_qualified_pilot_evidence"]
                    unresolved += bool(json.loads(row["country_evidence"])["unresolved"])
            if (_sorted_counts(counts), qualified, unresolved) != (
                result["counts"],
                result["qualified_pilot_rows"],
                result["unresolved_country_rows"],
            ):
                raise PilotError("Pilot artifact counts differ from the receipt")
    if any(receipt[key] != value for key, value in _totals(receipt["sources"]).items()):
        raise PilotError("Pilot grand totals do not reconcile")
    count_reports = reporting.generate(receipt["sources"], vocab)
    if set(count_reports) != COUNT_REPORTS or receipt["count_reports"] != {
        name: digest(content) for name, content in count_reports.items()
    }:
        raise PilotError("Pilot count reports do not match their source totals")
    for name, checksum in receipt["count_reports"].items():
        if sha256(root / name) != checksum:
            raise PilotError("Pilot count report differs from its receipt")
    review = receipt["review"]
    if review["artifact"] != "review/pilot-unmapped.csv":
        raise PilotError("Unexpected pilot review path")
    path = root / review["artifact"]
    if sha256(path) != review["sha256"]:
        raise PilotError("Pilot review queue differs from the receipt")
    distinct = occurrences = 0
    previous = None
    with path.open(newline="", encoding="utf-8") as stream:
        reader = csv.DictReader(stream)
        if reader.fieldnames != list(REVIEW_FIELDS):
            raise PilotError("Unexpected pilot review columns")
        for row in reader:
            identity = tuple(row[key] for key in REVIEW_FIELDS[:-1])
            terms = json.loads(row["unmapped"])
            _strings(terms, "Review unresolved terms")
            count = int(row["count"])
            if (
                (previous is not None and identity <= previous)
                or not terms
                or count < 1
                or row["source_id"] not in parents
            ):
                raise PilotError("Invalid, repeated or unsorted pilot review entry")
            previous = identity
            distinct += 1
            occurrences += count
    if (distinct, occurrences) != (review["distinct_terms"], review["record_term_occurrences"]):
        raise PilotError("Pilot review counts do not reconcile")
    scope = receipt["scope_review"]
    if scope["artifact"] != "review/pilot-scope-review.csv":
        raise PilotError("Unexpected source-scope review path")
    path = root / scope["artifact"]
    if sha256(path) != scope["sha256"]:
        raise PilotError("Source-scope review differs from the receipt")
    with path.open(newline="", encoding="utf-8") as stream:
        reader = csv.DictReader(stream)
        if reader.fieldnames != list(SCOPE_FIELDS):
            raise PilotError("Unexpected source-scope review columns")
        rows = list(reader)
    expected = next(
        (r["excluded_rows"] for r in receipt["sources"] if r["source_id"] == "usgs_global_ree"), 0
    )
    if (
        len(rows) != scope["rows"]
        or len(rows) != expected
        or (len({r["record_id"] for r in rows}) != len(rows))
        or any(r["decision"] or r["reviewer"] or r["source_id"] != "usgs_global_ree" for r in rows)
    ):
        raise PilotError("Source-scope review records do not reconcile")
    return receipt
