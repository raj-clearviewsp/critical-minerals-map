"""Bounded, provenance-preserving staging; no canonical facts or map publication."""

from __future__ import annotations

import hashlib
import importlib
import json
import math
import os
import sqlite3
import tempfile
from collections import Counter
from datetime import date, datetime
from pathlib import Path

import pandas as pd
import pandera.pandas as pa
import pyarrow as arrow
import pyarrow.parquet as parquet
import yaml

from pipeline.fetch.core import load_source, verify_snapshot
from pipeline.scaffold import json_bytes
from pipeline.vocab.audit import REQUIRED_SOURCES
from pipeline.vocab.audit import check as check_vocab

PARSERS = {
    **dict.fromkeys(
        (
            "usgs_global_ree",
            "usgs_global_critical",
            "usgs_us_critical",
            "usgs_laterites",
            "usgs_mrds",
        ),
        "geology",
    ),
    **dict.fromkeys(
        (
            "gseu_crm",
            "ga_global_ree",
            "ga_critical_2025",
            "ga_critical_2025_arcgis",
            "icmm",
            "wapha",
            "jasansky",
        ),
        "facilities",
    ),
    **dict.fromkeys(("usgs_mcs_2026", "oecd_export", "aiddata_transition_v2"), "context"),
}
JSON_FIELDS = (
    "aliases",
    "raw_commodities",
    "raw_deposit_type",
    "raw_status",
    "raw_size_grade_text",
    "reference",
    "raw_fields",
    "raw_geometry",
)
STRING_FIELDS = (
    "record_id",
    "source_id",
    "native_id",
    "record_kind",
    "raw_name",
    "location_precision",
    "country",
    *JSON_FIELDS,
    "source_file",
    "source_table",
    "source_url",
    "retrieved_at",
    "registered_at",
    "raw_sha256",
    "source_currency",
    "evidence_grade",
    "coordinate_issue",
)
FIELDS = (*STRING_FIELDS, "lat", "lon", "source_row")
NONNULL = {
    "record_id",
    "source_id",
    "native_id",
    "record_kind",
    *JSON_FIELDS,
    "source_file",
    "source_table",
    "source_url",
    "raw_sha256",
    "evidence_grade",
}
KINDS = {
    "deposit",
    "facility",
    "source_group",
    "country_commodity",
    "export_restriction",
    "finance",
    "finance_asset",
}
PRECISIONS = {"exact", "approximate", "district", "country centroid", "none"}
ARROW_SCHEMA = arrow.schema(
    [arrow.field(c, arrow.string(), nullable=c not in NONNULL) for c in STRING_FIELDS]
    + [
        arrow.field("lat", arrow.float64()),
        arrow.field("lon", arrow.float64()),
        arrow.field("source_row", arrow.int64(), nullable=False),
    ],
    metadata={b"atlas_schema": b"staging-source-record-v1"},
)
FRAME_SCHEMA = pa.DataFrameSchema(
    {
        **{c: pa.Column("string", nullable=c not in NONNULL) for c in STRING_FIELDS},
        "lat": pa.Column("Float64", pa.Check.in_range(-90, 90), nullable=True),
        "lon": pa.Column("Float64", pa.Check.in_range(-180, 180), nullable=True),
        "source_row": pa.Column("Int64", pa.Check.ge(1)),
    },
    strict=True,
    ordered=True,
    checks=[
        pa.Check(lambda df: df.record_kind.isin(KINDS).all()),
        pa.Check(lambda df: df.location_precision.dropna().isin(PRECISIONS).all()),
        pa.Check(lambda df: (df.lat.isna() == df.lon.isna()).all()),
    ],
    name="staging_source_record_v1",
)


class NormalizeError(ValueError):
    """A source record or staging receipt cannot be accepted."""


def sha256(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def clean(value):
    """JSON has no NaN; retain dates/bytes explicitly without inventing missing values."""
    if isinstance(value, dict):
        return {str(k): clean(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [clean(v) for v in value]
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, bytes):
        return {"encoding": "hex", "value": value.hex()}
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if hasattr(value, "item"):
        return clean(value.item())
    return value


def json_cell(value) -> str:
    return json.dumps(
        clean(value), ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    )


def coordinate_pair(lat, lon) -> tuple[float | None, float | None, str | None]:
    if lat is None and lon is None:
        return None, None, None
    try:
        latitude, longitude = float(lat), float(lon)
        if not math.isfinite(latitude) or not math.isfinite(longitude):
            raise ValueError("nonfinite")
        if not -90 <= latitude <= 90 or not -180 <= longitude <= 180:
            raise ValueError("range")
    except (TypeError, ValueError):
        return None, None, "Incomplete, nonnumeric or out-of-range coordinates; original retained"
    if latitude == longitude == 0:
        return None, None, "Zero/zero location requires review; original retained"
    return latitude, longitude, None


def make_row(source: dict, manifest: dict, files: dict, raw: dict) -> dict:
    allowed = {
        "native_id",
        "source_file",
        "source_table",
        "row_number",
        "record_kind",
        "raw_name",
        "aliases",
        "lat",
        "lon",
        "location_precision",
        "country",
        *JSON_FIELDS,
    }
    if set(raw) - allowed:
        raise NormalizeError(f"Unexpected parser fields: {sorted(set(raw) - allowed)}")
    for field in (
        "native_id",
        "source_file",
        "source_table",
        "row_number",
        "record_kind",
        "raw_fields",
    ):
        if raw.get(field) is None:
            raise NormalizeError(f"Missing parser identity/evidence: {field}")
    native = str(raw["native_id"])
    if not native.strip():
        raise NormalizeError("Empty native record ID")
    file = files.get(raw["source_file"])
    if file is None:
        raise NormalizeError(f"Parser read an unmanifested input: {raw['source_file']}")
    latitude, longitude, issue = coordinate_pair(raw.get("lat"), raw.get("lon"))
    precision = raw.get("location_precision") if latitude is not None else "none"
    if precision == "none" and latitude is not None:
        raise NormalizeError("Unlocated record carries coordinates")
    row = {c: None for c in FIELDS}
    row.update(
        {
            "record_id": f"{source['source_id']}:{native}",
            "source_id": source["source_id"],
            "native_id": native,
            "record_kind": raw["record_kind"],
            "raw_name": raw.get("raw_name"),
            "country": raw.get("country"),
            "lat": latitude,
            "lon": longitude,
            "coordinate_issue": issue,
            "location_precision": precision,
            "source_file": raw["source_file"],
            "source_table": raw["source_table"],
            "source_row": raw["row_number"],
            "source_url": file.get("url") or source["url"],
            "retrieved_at": file.get("retrieved_at", manifest.get("retrieved_at")),
            "registered_at": manifest.get("registered_at"),
            "raw_sha256": file["sha256"],
            "source_currency": source.get("currency_date", source.get("currency")),
            "evidence_grade": source["evidence_grade"],
        }
    )
    for field in JSON_FIELDS:
        row[field] = json_cell(raw.get(field, [] if field == "aliases" else {}))
    for field in STRING_FIELDS:
        if row[field] is not None and not isinstance(row[field], str):
            raise NormalizeError(f"Nontext {field}: {type(row[field]).__name__}")
    return row


def validate_batch(rows: list[dict]) -> arrow.Table:
    frame = pd.DataFrame.from_records(rows, columns=FIELDS).astype(
        {
            **dict.fromkeys(STRING_FIELDS, "string"),
            "lat": "Float64",
            "lon": "Float64",
            "source_row": "Int64",
        }
    )
    FRAME_SCHEMA.validate(frame, lazy=True)
    return arrow.Table.from_pandas(frame, schema=ARROW_SCHEMA, preserve_index=False)


def implementation_hashes(root: Path) -> dict:
    paths = [
        *sorted((root / "pipeline/normalize").glob("*.py")),
        *sorted((root / "sources").glob("*.yaml")),
        root / "uv.lock",
        root / "sources/normalization/expected_counts.yaml",
    ]
    return {p.relative_to(root).as_posix(): sha256(p) for p in paths}


def reconcile(profiles: list[dict], accepted: Counter, rejects: list[dict]) -> list[dict]:
    rejected = Counter((r["source_file"], r["source_table"]) for r in rejects)
    primary = set()
    output = []
    for profile in profiles:
        identity = (profile["file"], profile["table"])
        item = dict(profile)
        if profile["role"] == "primary":
            if identity in primary:
                raise NormalizeError(f"Duplicate primary table profile: {identity}")
            primary.add(identity)
            item.update(accepted=accepted[identity], rejected=rejected[identity])
            if profile["rows"] != item["accepted"] + item["rejected"]:
                raise NormalizeError(f"Source row reconciliation failed: {identity}: {item}")
        output.append(item)
    if (set(accepted) | set(rejected)) - primary:
        raise NormalizeError("Emitted/rejected rows lack a primary table reconciliation")
    if any(not r.get("reason") for r in rejects):
        raise NormalizeError("Every rejected row needs a reason")
    return output


def stage_source(
    root: Path, source_id: str, temp: Path, expected_snapshot: dict | None = None
) -> dict:
    source = load_source(root, source_id)
    snapshots = sorted((root / "data/raw" / source_id).glob("????-??-??"))
    if not snapshots:
        raise NormalizeError(f"Missing snapshot: {source_id}")
    snapshot = snapshots[-1]
    manifest = verify_snapshot(snapshot)
    if manifest["source_id"] != source_id:
        raise NormalizeError("Snapshot source identity does not match its registry")
    if expected_snapshot is not None and (
        snapshot.relative_to(root).as_posix() != expected_snapshot["snapshot"]
        or sha256(snapshot / "manifest.json") != expected_snapshot["manifest_sha256"]
    ):
        raise NormalizeError("Raw snapshot changed since the vocabulary gate")
    files = {
        (snapshot / e["path"]).relative_to(root / "data/raw").as_posix(): e
        for e in manifest["files"]
    }
    module = importlib.import_module(f"pipeline.normalize.{PARSERS[source_id]}")
    parsed = module.parse(root, source_id)
    target = temp / f"{source_id}.parquet"
    accepted, kinds, locations, issues = Counter(), Counter(), Counter(), Counter()
    count = 0
    with sqlite3.connect(temp / f"{source_id}-ids.sqlite") as ids:
        ids.execute("CREATE TABLE ids (record_id TEXT PRIMARY KEY, source_row BLOB UNIQUE)")
        with parquet.ParquetWriter(
            target, ARROW_SCHEMA, compression="zstd", version="2.6"
        ) as writer:
            batch = []
            for original in parsed["rows"]:
                row = make_row(source, manifest, files, original)
                count += 1
                accepted[(row["source_file"], row["source_table"])] += 1
                kinds[row["record_kind"]] += 1
                locations["located" if row["lat"] is not None else "unlocated"] += 1
                if row["coordinate_issue"]:
                    issues[row["coordinate_issue"]] += 1
                batch.append(row)
                if len(batch) == 10000:
                    _write_batch(writer, ids, batch)
                    batch = []
            if batch:
                _write_batch(writer, ids, batch)
        rejected_rows = set()
        for rejection in parsed["rejects"]:
            identity = source_row_key(
                rejection["source_file"], rejection["source_table"], rejection["row_number"]
            )
            if (
                identity in rejected_rows
                or ids.execute("SELECT 1 FROM ids WHERE source_row = ?", (identity,)).fetchone()
            ):
                raise NormalizeError("A rejected row is repeated or also accepted")
            rejected_rows.add(identity)
    tables = reconcile(parsed["tables"], accepted, parsed["rejects"])
    return {
        "source_id": source_id,
        "rows": count,
        "kinds": dict(kinds),
        "locations": dict(locations),
        "coordinate_issues": dict(issues),
        "snapshot": snapshot.relative_to(root).as_posix(),
        "manifest_sha256": sha256(snapshot / "manifest.json"),
        "files": {name: e["sha256"] for name, e in files.items()},
        "artifact": f"data/staging/{source_id}.parquet",
        "sha256": sha256(target),
        "tables": tables,
        "rejects": clean(parsed["rejects"]),
        "notes": parsed["notes"],
    }


def _write_batch(writer, ids, batch):
    try:
        ids.executemany(
            "INSERT INTO ids VALUES (?, ?)",
            (
                (
                    r["record_id"],
                    source_row_key(r["source_file"], r["source_table"], r["source_row"]),
                )
                for r in batch
            ),
        )
    except sqlite3.IntegrityError as error:
        raise NormalizeError("Duplicate source-native identity or physical source row") from error
    writer.write_table(validate_batch(batch), row_group_size=10000)


def source_row_key(file: str, table: str, number: int) -> bytes:
    if not isinstance(number, int) or isinstance(number, bool) or number < 1:
        raise NormalizeError("Source row number must be a positive integer")
    return hashlib.sha256(json_cell([file, table, number]).encode()).digest()


def normalize(root: Path, progress=None) -> dict:
    """Accept all source outputs only after every parser and reconciliation passes."""
    root = root.resolve()
    vocab = check_vocab(root)
    if set(vocab["sources"]) != REQUIRED_SOURCES:
        raise NormalizeError("Vocabulary gate does not cover the acquired inventory")
    incoming = root / "data/.incoming"
    incoming.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="normalize-", dir=incoming) as temporary:
        temp = Path(temporary)
        results = []
        for source_id in sorted(PARSERS):
            result = stage_source(root, source_id, temp, vocab["sources"][source_id])
            results.append(result)
            if progress is not None:
                progress(result)
        validate_expected_counts(root, results)
        report = {
            "schema_version": "normalization-v1",
            "stage": "normalize",
            "phase": 0,
            "step": 4,
            "status": "pass",
            "rows": sum(r["rows"] for r in results),
            "rejects": sum(len(r["rejects"]) for r in results),
            "sources": results,
            "implementation": implementation_hashes(root),
            "vocabulary_audit_sha256": sha256(root / "reports/vocabulary-audit.json"),
            "not_staged": [
                {
                    "source_id": "usgs_critical_list_2025",
                    "reason": "Vocabulary authority, not an observation dataset",
                }
            ],
            "canonical_rows": 0,
            "published_rows": 0,
        }
        staged = [(temp / f"{r['source_id']}.parquet", root / r["artifact"]) for r in results]
        receipt = temp / "normalization-audit.json"
        receipt.write_bytes(json_bytes(report))
        staged.append((receipt, root / "reports/normalization-audit.json"))
        accept_outputs(staged)
    return report


def check(root: Path, *, raw: bool = False) -> dict:
    check_vocab(root)
    receipt = json.loads((root / "reports/normalization-audit.json").read_text())
    if receipt["implementation"] != implementation_hashes(root):
        raise NormalizeError("Normalization implementation or source registry changed")
    if receipt["vocabulary_audit_sha256"] != sha256(root / "reports/vocabulary-audit.json"):
        raise NormalizeError("Vocabulary audit changed since normalization")
    if receipt["status"] != "pass" or {r["source_id"] for r in receipt["sources"]} != set(PARSERS):
        raise NormalizeError("Incomplete staging source inventory")
    validate_expected_counts(root, receipt["sources"])
    for result in receipt["sources"]:
        if raw:
            snapshot = root / result["snapshot"]
            verify_snapshot(snapshot)
            if sha256(snapshot / "manifest.json") != result["manifest_sha256"]:
                raise NormalizeError("Raw snapshot manifest changed")
            artifact = root / result["artifact"]
            if sha256(artifact) != result["sha256"]:
                raise NormalizeError("Staging output differs from receipt")
            file = parquet.ParquetFile(artifact)
            if file.metadata.num_rows != result["rows"] or not file.schema_arrow.equals(
                ARROW_SCHEMA
            ):
                raise NormalizeError("Staging schema or row count differs from receipt")
        accepted = Counter(
            {
                (t["file"], t["table"]): t["accepted"]
                for t in result["tables"]
                if t["role"] == "primary"
            }
        )
        reconcile(result["tables"], accepted, result["rejects"])
        if sum(accepted.values()) != result["rows"]:
            raise NormalizeError("Source totals do not reconcile")
    if sum(r["rows"] for r in receipt["sources"]) != receipt["rows"]:
        raise NormalizeError("Staging grand total does not reconcile")
    return receipt


def validate_expected_counts(root: Path, results: list[dict]) -> None:
    expected = yaml.safe_load((root / "sources/normalization/expected_counts.yaml").read_text())
    if expected["schema_version"] != "normalization-counts-v1" or set(expected["sources"]) != set(
        PARSERS
    ):
        raise NormalizeError("Missing independent release-count expectations")
    if len(results) != len(PARSERS) or {r["source_id"] for r in results} != set(PARSERS):
        raise NormalizeError("Duplicate or missing source results")
    for result in results:
        entry = expected["sources"][result["source_id"]]
        if result["rows"] != entry["rows"] or len(result["rejects"]) != entry["rejects"]:
            raise NormalizeError(f"Release count mismatch: {result['source_id']}")


def accept_outputs(staged: list[tuple[Path, Path]]) -> None:
    """Receipt last marks acceptance; roll back new links on ordinary commit failures."""
    for original, destination in staged:
        if destination.exists() and sha256(original) != sha256(destination):
            raise NormalizeError(f"Existing staging artifact differs: {destination}")
    created = []
    try:
        for original, destination in staged:
            if destination.exists():
                continue
            destination.parent.mkdir(parents=True, exist_ok=True)
            os.link(original, destination)
            created.append((original, destination))
    except OSError:
        for original, destination in reversed(created):
            if destination.exists() and os.path.samestat(original.stat(), destination.stat()):
                destination.unlink()
        raise
