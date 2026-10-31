"""Immutable triage revision and a small usability pilot, without entity merges."""

from __future__ import annotations

import csv
import io
import json
import re
import tempfile
from collections import Counter, defaultdict
from pathlib import Path

import pyarrow.parquet as parquet

from pipeline.link.candidates import haversine_km
from pipeline.link.gold_review import check as check_original
from pipeline.link.scope import review_eligibility
from pipeline.normalize.core import accept_outputs, sha256
from pipeline.scaffold import digest, json_bytes
from pipeline.triage.features import enrich_record, reconcile_source_views
from pipeline.triage.labels import PILOT_SIZE, blank_csv, preserve_import, sample_digest
from pipeline.triage.reporting import render_report
from pipeline.triage.review_page import render_pilot
from pipeline.triage.rules import triage_pairs
from pipeline.triage.units import build_review_units
from pipeline.vocab.core import Vocabulary

DIRECTORY = "data/review/triage-v2"
RECEIPT = "reports/triage-audit.json"
REPORT = "reports/phase0-step06-triage.md"
COUNTS = "reports/triage-counts.csv"
QUEUES = ("likely_match", "likely_distinct", "relationship", "ambiguous")
QUOTAS = {"likely_match": 3, "likely_distinct": 3, "relationship": 7, "ambiguous": 7}
SEED = "atlas-triage-pilot-v2"
PRIVATE_NAMES = (
    "original-decisions.csv",
    "preserved-decisions.json",
    "records.json",
    "triage-pairs.json",
    "triage-pairs.csv",
    "source-views.json",
    "prior-decision-checks.json",
    "pilot.json",
    "pilot-blank.csv",
    "index.html",
    "README.txt",
    "review-units.json",
)


class TriageError(ValueError):
    """A source revision, imported decision or accepted triage artifact changed."""


def implementation_hashes(root: Path) -> dict:
    return {
        path.relative_to(root).as_posix(): sha256(path)
        for path in [*sorted((root / "pipeline/triage").glob("*.py")), root / "uv.lock"]
    }


def load_records(root: Path) -> dict:
    parent = json.loads((root / "reports/pilot-audit.json").read_text())
    vocab = Vocabulary(root)
    records = {}
    for source in parent["sources"]:
        for batch in parquet.ParquetFile(root / source["artifact"]).iter_batches(batch_size=10000):
            for row in batch.to_pylist():
                if not review_eligibility(row)["eligible"]:
                    continue
                record = enrich_record(row, vocab)
                if record["record_id"] in records:
                    raise TriageError("Duplicate eligible source record")
                records[record["record_id"]] = record
    return records


def _rank(value: str) -> str:
    return digest((SEED + ":" + value).encode())


def _structural(row: dict) -> bool:
    return row["queue"] in {"relationship", "ambiguous"} or any(
        any(token in reason for token in ("numeric", "number", "direction"))
        for reason in row["reason_codes"]
    )


def _topics(row: dict) -> set[str]:
    features = row.get("features", {})
    flags = set(features.get("left_scope_flags", []) + features.get("right_scope_flags", []))
    tests = {
        "numbered_identifiers": features.get("numeric_conflict"),
        "directional_identifiers": features.get("direction_qualifier_difference"),
        "entity_granularity": row["queue"] == "relationship",
        "publisher_clusters": "publisher_cluster_record" in flags,
        "alias_roles": features.get("context_exact_names")
        or features.get("unverified_exact_names"),
        "reused_coordinates": features.get("coordinate_reuse"),
        "subsites": "subsite"
        in {features.get("left_entity_level"), features.get("right_entity_level")},
        "geology": features.get("deposit_type_comparison") == "different_reported_codes",
        "facility_scope": "name_suggests_processing_facility" in flags,
        "country": features.get("country_conflict"),
    }
    return {key for key, value in tests.items() if value}


def select_cases(rows: list[dict], previous: dict) -> tuple[list[dict], dict]:
    """Select six clarified prior cases and fourteen new cases, with explicit quotas.

    This is a deliberately informative usability pilot, never a random accuracy
    sample. Original labels are not used to fit or rank the triage classifier.
    """
    if len({row["pair_id"] for row in rows}) != len(rows):
        raise TriageError("Duplicate triage pair IDs")
    selected, selected_ids, queue_counts = [], set(), Counter()
    seen_sources, seen_families, seen_reasons, source_counts = set(), set(), set(), Counter()
    seen_topics = set()

    def choose(pool: list[dict]) -> dict:
        def priority(row):
            source = tuple(row["source_pair"])
            # Prefer a range of concrete problems and avoid several cases from
            # the same pair of sources before others have been represented.
            return (
                -len(_topics(row) - seen_topics),
                source_counts[source],
                -len(set(row["reason_codes"]) - seen_reasons),
                -len(set(row["shared_families"]) - seen_families),
                -len(set(row["source_pair"]) - seen_sources),
                _rank(row["pair_id"]),
            )

        return min(pool, key=priority)

    def add(row):
        selected.append(row)
        selected_ids.add(row["pair_id"])
        queue_counts[row["queue"]] += 1
        source_counts[tuple(row["source_pair"])] += 1
        seen_sources.update(row["source_pair"])
        seen_families.update(row["shared_families"])
        seen_reasons.update(row["reason_codes"])
        seen_topics.update(_topics(row))

    candidates = [
        row
        for row in rows
        if row["pair_id"] in previous
        and _structural(row)
        and row["disposition"] != "source_view_duplicate"
    ]
    while len(selected) < 6:
        pool = [
            row
            for row in candidates
            if row["pair_id"] not in selected_ids
            and queue_counts[row["queue"]] < QUOTAS[row["queue"]]
        ]
        if not pool:
            break
        add(choose(pool))
    revisited = len(selected)
    for queue in QUEUES:
        while queue_counts[queue] < QUOTAS[queue]:
            pool = [
                row
                for row in rows
                if row["queue"] == queue
                and row["pair_id"] not in selected_ids
                and row["pair_id"] not in previous
                and row["disposition"] != "source_view_duplicate"
            ]
            if not pool:
                raise TriageError(f"Insufficient new cases for pilot quota: {queue}")
            add(choose(pool))
    if len(selected) != PILOT_SIZE:
        raise TriageError("Pilot does not contain exactly twenty distinct cases")
    # Interleave the categories; do not open with a long run of the same problem.
    selected.sort(key=lambda row: _rank("display:" + row["pair_id"]))
    return selected, {
        "seed": SEED,
        "size": PILOT_SIZE,
        "queue_quotas": dict(QUOTAS),
        "previous_decisions_shown": revisited,
        "new_cases": PILOT_SIZE - revisited,
        "excluded_from_pilot": "Verified source-view representations need no entity-review vote.",
        "purpose": "Usability and ambiguity pilot; not the formal 200-pair evaluation set.",
        "selection": (
            "Six structurally flagged prior cases where available; diverse new cases fill quotas."
        ),
        "new_labels": 0,
    }


def prior_checks(rows: list[dict], decisions: list[dict]) -> list[dict]:
    indexed = {row["pair_id"]: row for row in rows}
    result = []
    for decision in decisions:
        if decision["pair_id"] not in indexed:
            raise TriageError("A preserved decision is missing from the candidate universe")
        row = indexed[decision["pair_id"]]
        result.append(
            {
                "original_decision": decision,
                "machine_queue": row["queue"],
                "machine_disposition": row["disposition"],
                "reason_codes": row["reason_codes"],
                "clarification_flag": _structural(row),
                "policy": "Flag is a request for clarification, not a replacement human label.",
            }
        )
    return result


def unit_representatives(rows: list[dict], units: dict, previous: dict) -> list[dict]:
    """Keep a prior reviewed pair as its unit's face where possible; never alter its label."""
    indexed = {row["pair_id"]: row for row in rows}
    result = []
    for unit in units["units"]:
        prior = sorted(set(unit["member_pair_ids"]) & previous.keys())
        identity = prior[0] if prior else unit["representative_pair_id"]
        row = indexed[identity]
        reasons = sorted(set(row["reason_codes"]) | set(unit["reason_codes"]))
        explanation = row["explanation"]
        if unit["queue_conflict"]:
            reasons.append("source_view_routing_conflict")
            explanation += (
                " Equivalent publisher views received different routes; clarify this conflict."
            )
        result.append(
            {
                **row,
                "queue": unit["queue"],
                "disposition": (
                    "source_view_routing_conflict" if unit["queue_conflict"] else row["disposition"]
                ),
                "requires_adjudication": unit["requires_adjudication"],
                "reason_codes": sorted(set(reasons)),
                "explanation": explanation,
                "review_unit_id": unit["unit_id"],
                "equivalent_pair_ids": unit["member_pair_ids"],
            }
        )
    return result


def neighbors(case: dict, records: dict, rows: list[dict], groups: list) -> tuple[list[dict], int]:
    sides = {case["left_record_id"], case["right_record_id"]}
    ids = set()
    for row in rows:
        pair = {row["left_record_id"], row["right_record_id"]}
        if pair & sides:
            ids.update(pair - sides)
    group_of = {identity: min(group) for group in groups for identity in group}
    representatives = {}
    side_groups = {group_of.get(identity, identity) for identity in sides}
    for identity in sorted(ids):
        group = group_of.get(identity, identity)
        if group not in side_groups:
            representatives.setdefault(group, identity)

    def separation(identity):
        point = records[identity]
        return min(
            haversine_km(point["lat"], point["lon"], records[side]["lat"], records[side]["lon"])
            for side in sides
        )

    ordered = sorted(
        representatives.values(), key=lambda identity: (separation(identity), identity)
    )
    fields = ("record_id", "display_name", "lat", "lon", "entity_level", "source_id")
    return [{key: records[identity][key] for key in fields} for identity in ordered[:8]], len(
        ordered
    )


def csv_pairs(rows: list[dict]) -> bytes:
    output = io.StringIO(newline="")
    fields = (
        "pair_id",
        "left_record_id",
        "right_record_id",
        "queue",
        "disposition",
        "distance_km",
        "shared_families",
        "reason_codes",
        "explanation",
    )
    writer = csv.DictWriter(output, fieldnames=fields, lineterminator="\n")
    writer.writeheader()
    for row in rows:
        item = {key: row[key] for key in fields}
        for key in ("shared_families", "reason_codes"):
            item[key] = json.dumps(item[key], ensure_ascii=False)
        # Only explanation can contain arbitrary source text in this flat export.
        text = str(item["explanation"])
        if re.match(r"\s*[=+@-]", text):
            item["explanation"] = "'" + text
        writer.writerow(item)
    return output.getvalue().encode()


def counts_csv(receipt: dict) -> bytes:
    output = io.StringIO(newline="")
    writer = csv.writer(output, lineterminator="\n")
    writer.writerow(["dimension", "category", "count"])
    for dimension in ("queue_counts", "disposition_counts", "reason_counts", "entity_levels"):
        writer.writerows(
            [dimension, key, value] for key, value in sorted(receipt[dimension].items())
        )
    return output.getvalue().encode()


def _counts(rows: list[dict], records: dict, checks: list[dict]) -> dict:
    sources = defaultdict(lambda: Counter())
    for row in rows:
        sources[" | ".join(row["source_pair"])][row["queue"]] += 1
    return {
        "eligible_records": len(records),
        "candidate_pairs": len(rows),
        "queue_counts": dict(sorted(Counter(row["queue"] for row in rows).items())),
        "disposition_counts": dict(sorted(Counter(row["disposition"] for row in rows).items())),
        "reason_counts": dict(
            sorted(Counter(reason for row in rows for reason in set(row["reason_codes"])).items())
        ),
        "entity_levels": dict(
            sorted(Counter(row["entity_level"] for row in records.values()).items())
        ),
        "record_scope_flags": dict(
            sorted(
                Counter(
                    flag for row in records.values() for flag in set(row["scope_flags"])
                ).items()
            )
        ),
        "informative_deposit_type_records": sum(
            bool(row["deposit_types"]) for row in records.values()
        ),
        "source_pair_queues": {
            key: dict(sorted(value.items())) for key, value in sorted(sources.items())
        },
        "prior_decision_queues": dict(
            sorted(Counter(row["machine_queue"] for row in checks).items())
        ),
        "prior_clarification_flags": sum(row["clarification_flag"] for row in checks),
    }


def build(root: Path, source_csv: Path) -> tuple[dict, dict]:
    original = check_original(root, raw=True)
    sample = json.loads((root / "data/review/linkage/sample.json").read_text())
    csv_bytes, preserved = preserve_import(source_csv, sample)
    records = load_records(root)
    if len(records) != original["eligible_rows"]:
        raise TriageError("Original eligible record universe changed")
    candidates = json.loads((root / "data/review/linkage/candidates.json").read_text())
    views = reconcile_source_views(list(records.values()))
    rows = triage_pairs(candidates, records, views)
    if len(rows) != len(candidates) or {row["pair_id"] for row in rows} != {
        row["pair_id"] for row in candidates
    }:
        raise TriageError("Triage dropped, invented or duplicated candidate pairs")
    previous = {row["pair_id"]: row for row in preserved["decisions"]}
    checks = prior_checks(rows, preserved["decisions"])
    units = build_review_units(rows, views)
    chosen, selection = select_cases(unit_representatives(rows, units, previous), previous)
    cases = []
    for row in chosen:
        nearby, count = neighbors(row, records, rows, views["groups"])
        cases.append(
            {
                **row,
                "left": records[row["left_record_id"]],
                "right": records[row["right_record_id"]],
                "neighbors": nearby,
                "neighbor_count": count,
                "previous_decision": previous.get(row["pair_id"]),
                "label": "",
                "reviewer": "",
                "notes": "",
            }
        )
    pilot = {
        "schema_version": "triage-pilot-v2",
        "cases": cases,
        "selection": selection,
        "original_sample_sha256": original["sample_sha256"],
        "preserved_decisions_sha256": digest(json_bytes(preserved)),
    }
    pilot["sample_sha256"] = sample_digest(pilot)
    metadata = {
        "sample_sha256": pilot["sample_sha256"],
        "title": "Review 20 focused site comparisons",
        "original_sample_sha256": original["sample_sha256"],
        "preserved_decision_count": preserved["reviewed_rows"],
        "selection": selection,
        "source_names": sample["source_names"],
    }
    readme = (
        "Critical Minerals Atlas — focused triage pilot\n\n"
        "Open index.html in a browser. This is a 20-case usability/ambiguity pilot, not the "
        "formal 200-pair gold evaluation. Machine triage is a suggestion, not a human label "
        "or calibrated probability. No records have been merged.\n\n"
        "Your original CSV is preserved byte-for-byte in original-decisions.csv; all its "
        "nonblank labels, reviewer names and notes are copied unchanged into "
        "preserved-decisions.json. "
        "Any original decision shown in a pilot case is read-only. Every NEW pilot decision starts "
        "blank and is exported separately. Use the relationship options for "
        "districts/components.\n\n"
        "The local reference map shows source coordinates, neighboring records, a scale and north. "
        "It has no terrain basemap and does not claim coordinate accuracy. Shared coordinates "
        "do not establish identity. Source details and original record links retain evidence.\n\n"
        "Export progress regularly. Browser storage is not a backup. Exported CSVs contain all "
        "20 cases and a sample hash. Validate from the repository with:\n"
        "uv run --locked python -m pipeline.triage --labels /absolute/path/to/export.csv\n\n"
        "All detailed source/pair/reviewer files remain local and Git-ignored. The public "
        "triage report contains aggregates only. Keep original artifacts unchanged.\n"
    ).encode()
    contents = (
        csv_bytes,
        json_bytes(preserved),
        json_bytes(records),
        json_bytes(rows),
        csv_pairs(rows),
        json_bytes(views),
        json_bytes(checks),
        json_bytes(pilot),
        blank_csv(cases, pilot["sample_sha256"]),
        render_pilot(cases, metadata),
        readme,
        json_bytes(units),
    )
    outputs = {
        f"{DIRECTORY}/{name}": value for name, value in zip(PRIVATE_NAMES, contents, strict=True)
    }
    receipt = {
        "schema_version": "triage-audit-v2",
        "phase": 0,
        "step": 6,
        "status": "focused_pilot_ready",
        "model_fitted": False,
        "auto_merges": 0,
        "canonical_rows_added": 0,
        "human_labels_created": 0,
        "published_rows_added": 0,
        "original_review_audit_sha256": sha256(root / "reports/linkage-review-audit.json"),
        "original_sample_sha256": original["sample_sha256"],
        "original_candidates": original["candidate_pairs"],
        "sample_sha256": pilot["sample_sha256"],
        "selection": selection,
        "import": {
            key: preserved[key]
            for key in (
                "original_csv_sha256",
                "original_sample_sha256",
                "csv_rows",
                "reviewed_rows",
                "counts",
            )
        },
        "source_view_pairs": len(views["pairs"]),
        "source_view_groups": len(views["groups"]),
        "workload": units["summary"],
        **_counts(rows, records, checks),
        "implementation": implementation_hashes(root),
        "privacy": "Detailed records, reviewer names and notes remain local and unpublished.",
        "limits": [
            "Queues are provisional rules, not truth labels or probabilities.",
            "Original 10 km candidate universe is retained; no blocking-recall claim is made.",
            "Missing geology is neutral; uncertain source granularity remains visible.",
            (
                "The 20-case pilot cannot establish 97% auto-merge precision "
                "or complete the 200-pair gate."
            ),
        ],
    }
    outputs[COUNTS] = counts_csv(receipt)
    outputs[REPORT] = render_report(receipt)
    receipt["artifacts"] = {
        name: {"sha256": digest(content), "bytes": len(content)}
        for name, content in sorted(outputs.items())
    }
    validate_receipt(receipt, original)
    return outputs, receipt


def prepare(root: Path, decisions: Path | None = None) -> dict:
    root = root.resolve()
    source_csv = decisions or root / DIRECTORY / "original-decisions.csv"
    outputs, receipt = build(root, source_csv)
    outputs[RECEIPT] = json_bytes(receipt)
    with tempfile.TemporaryDirectory(prefix=".atlas-triage-", dir=root / "data") as temporary:
        temp = Path(temporary)
        staged = []
        for relative, content in outputs.items():
            path = temp / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
            staged.append((path, root / relative))
        accept_outputs(staged)
    return receipt


def validate_receipt(receipt: dict, original: dict) -> None:
    if (
        (receipt["schema_version"], receipt["phase"], receipt["step"], receipt["status"])
        != ("triage-audit-v2", 0, 6, "focused_pilot_ready")
        or receipt["model_fitted"] is not False
        or any(
            receipt[key] != 0
            for key in (
                "auto_merges",
                "canonical_rows_added",
                "human_labels_created",
                "published_rows_added",
            )
        )
        or receipt["original_candidates"] != original["candidate_pairs"]
        or receipt["candidate_pairs"] != original["candidate_pairs"]
        or receipt["eligible_records"] != original["eligible_rows"]
        or receipt["original_sample_sha256"] != original["sample_sha256"]
    ):
        raise TriageError("Invalid triage checkpoint or changed original universe")
    for dimension in (
        "queue_counts",
        "disposition_counts",
        "reason_counts",
        "entity_levels",
        "record_scope_flags",
        "prior_decision_queues",
    ):
        if any(type(value) is not int or value < 0 for value in receipt[dimension].values()):
            raise TriageError("Invalid aggregate triage counts")
    if set(receipt["queue_counts"]) - set(QUEUES) or (
        sum(receipt["queue_counts"].values()) != receipt["candidate_pairs"]
        or sum(receipt["disposition_counts"].values()) != receipt["candidate_pairs"]
        or sum(receipt["entity_levels"].values()) != receipt["eligible_records"]
    ):
        raise TriageError("Triage totals do not reconcile")
    if (
        type(receipt["source_view_pairs"]) is not int
        or receipt["source_view_pairs"] != receipt["source_view_groups"]
        or receipt["source_view_pairs"]
        != receipt["disposition_counts"].get("source_view_duplicate", 0)
        or type(receipt["informative_deposit_type_records"]) is not int
        or not 0 <= receipt["informative_deposit_type_records"] <= receipt["eligible_records"]
        or any(value > receipt["candidate_pairs"] for value in receipt["reason_counts"].values())
        or any(
            value > receipt["eligible_records"] for value in receipt["record_scope_flags"].values()
        )
    ):
        raise TriageError("Source-view or record-feature aggregates do not reconcile")
    work = receipt["workload"]
    if (
        work["input_candidate_count"] != receipt["candidate_pairs"]
        or work["source_view_internal_pairs"] != receipt["source_view_pairs"]
        or work["review_units"]
        + work["source_view_internal_pairs"]
        + work["duplicate_comparisons_collapsed"]
        != receipt["candidate_pairs"]
        or sum(work["unit_queue_counts"].values()) != work["review_units"]
        or set(work["unit_queue_counts"]) - set(QUEUES)
        or any(
            type(value) is not int or value < 0
            for value in (
                work["input_candidate_count"],
                work["source_view_internal_pairs"],
                work["review_units"],
                work["duplicate_comparisons_collapsed"],
                *work["unit_queue_counts"].values(),
            )
        )
    ):
        raise TriageError("Source-view review units do not reconcile")
    queues = Counter()
    for values in receipt["source_pair_queues"].values():
        if set(values) - set(QUEUES) or any(type(v) is not int or v < 0 for v in values.values()):
            raise TriageError("Invalid source-pair queue counts")
        queues.update(values)
    if dict(sorted(queues.items())) != receipt["queue_counts"]:
        raise TriageError("Source-pair queues do not reconcile")
    imported, selection = receipt["import"], receipt["selection"]
    if (
        set(imported["counts"]) != {"blank", "same_site", "different_sites", "uncertain"}
        or any(type(value) is not int or value < 0 for value in imported["counts"].values())
        or type(imported["csv_rows"]) is not int
        or type(imported["reviewed_rows"]) is not int
        or imported["csv_rows"] != 200
        or sum(imported["counts"].values()) != 200
        or imported["reviewed_rows"] != 200 - imported["counts"]["blank"]
        or imported["original_sample_sha256"] != original["sample_sha256"]
        or sum(receipt["prior_decision_queues"].values()) != imported["reviewed_rows"]
        or not 0 <= receipt["prior_clarification_flags"] <= imported["reviewed_rows"]
        or selection["size"] != PILOT_SIZE
        or selection["queue_quotas"] != QUOTAS
        or selection["seed"] != SEED
        or selection["new_labels"] != 0
        or type(selection["previous_decisions_shown"]) is not int
        or type(selection["new_cases"]) is not int
        or selection["previous_decisions_shown"] + selection["new_cases"] != PILOT_SIZE
        or not 0 <= selection["previous_decisions_shown"] <= min(6, imported["reviewed_rows"])
    ):
        raise TriageError("Preserved-decision or pilot counts do not reconcile")
    paths = {f"{DIRECTORY}/{name}" for name in PRIVATE_NAMES} | {COUNTS, REPORT}
    if set(receipt["artifacts"]) != paths:
        raise TriageError("Unexpected triage artifact paths")
    for item in receipt["artifacts"].values():
        if (
            not re.fullmatch(r"[0-9a-f]{64}", str(item["sha256"]))
            or type(item["bytes"]) is not int
            or item["bytes"] < 1
        ):
            raise TriageError("Invalid artifact checksum or size")
    for value in (receipt["sample_sha256"], imported["original_csv_sha256"]):
        if not re.fullmatch(r"[0-9a-f]{64}", str(value)):
            raise TriageError("Invalid accepted sample/input checksum")
    if (
        receipt["artifacts"][f"{DIRECTORY}/original-decisions.csv"]["sha256"]
        != imported["original_csv_sha256"]
    ):
        raise TriageError("Preserved original CSV checksum differs from its import")


def check(root: Path, *, raw=False) -> dict:
    original = check_original(root, raw=False)
    receipt = json.loads((root / RECEIPT).read_text())
    validate_receipt(receipt, original)
    if receipt["implementation"] != implementation_hashes(root):
        raise TriageError("Triage implementation changed since the accepted build")
    if receipt["original_review_audit_sha256"] != sha256(
        root / "reports/linkage-review-audit.json"
    ):
        raise TriageError("Original review receipt changed")
    count_bytes = counts_csv(receipt)
    if (root / COUNTS).read_bytes() != count_bytes or (
        receipt["artifacts"][COUNTS] != {"sha256": digest(count_bytes), "bytes": len(count_bytes)}
    ):
        raise TriageError("Aggregate count export differs from its receipt")
    report_bytes = render_report(receipt)
    if (root / REPORT).read_bytes() != report_bytes or (
        receipt["artifacts"][REPORT] != {"sha256": digest(report_bytes), "bytes": len(report_bytes)}
    ):
        raise TriageError("Triage report differs from its receipt")
    if raw:
        outputs, rebuilt = build(root, root / DIRECTORY / "original-decisions.csv")
        if rebuilt != receipt:
            raise TriageError("Rebuilt triage receipt differs from accepted results")
        for path, content in outputs.items():
            if (root / path).read_bytes() != content:
                raise TriageError("Private triage artifact changed")
    return receipt
