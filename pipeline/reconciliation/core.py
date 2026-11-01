"""Freeze research, preserved human decisions and a blinded validation preparation."""

from __future__ import annotations

import csv
import io
import json
import re
import tempfile
from collections import Counter
from pathlib import Path

from pipeline.normalize.core import accept_outputs, sha256
from pipeline.reconciliation.evidence import reconcile_evidence
from pipeline.reconciliation.ledger import DIRECTORY, build_ledger
from pipeline.reconciliation.reporting import render_index, render_report
from pipeline.reconciliation.review_page import render_review, sanitize_cases
from pipeline.reconciliation.sampling import select_benchmark, validate_selection
from pipeline.scaffold import digest, json_bytes
from pipeline.triage.core import check as check_triage
from pipeline.triage.core import neighbors
from pipeline.triage.labels import LABELS, blank_csv, sample_digest

RECEIPT = "reports/reconciliation-audit.json"
REPORT = "reports/phase0-step06-reconciliation.md"
PACKET_MODES = ("adjudication", "development", "holdout")
PRIVATE_FILES = (
    "completed-pilot.csv",
    "decision-history.json",
    "latest-decisions.json",
    "pilot-outcomes.json",
    "source-research-input.json",
    "source-research.json",
    "benchmark.json",
    "index.html",
    *(f"{mode}{suffix}" for mode in PACKET_MODES for suffix in (".json", "-blank.csv", ".html")),
)


def implementation_hashes(root: Path) -> dict:
    return {
        p.relative_to(root).as_posix(): sha256(p)
        for p in [*sorted((root / "pipeline/reconciliation").glob("*.py")), root / "uv.lock"]
    }


def _read(root: Path, name: str):
    return json.loads((root / name).read_text())


def _case(row: dict, records: dict, candidates: list, views: dict, previous=None, research=None):
    nearby, count = neighbors(row, records, candidates, views["groups"])
    return {
        **row,
        "left": records[row["left_record_id"]],
        "right": records[row["right_record_id"]],
        "neighbors": nearby,
        "neighbor_count": count,
        "previous_decision": previous,
        "research": research,
        "label": "",
        "reviewer": "",
        "notes": "",
    }


def _packet(cases: list, mode: str, source_names: dict) -> tuple[dict, dict]:
    cleaned = sanitize_cases(cases, mode)
    sample = {"schema_version": "validation-review-v1", "mode": mode, "cases": cleaned}
    sample["sample_sha256"] = sample_digest(sample)
    metadata = {
        "sample_sha256": sample["sample_sha256"],
        "mode": mode,
        "title": {
            "adjudication": "Review the source findings",
            "development": "Development validation",
            "holdout": "Blinded validation",
        }[mode],
        "source_names": source_names,
    }
    prefix = f"{DIRECTORY}/{mode}"
    outputs = {
        prefix + ".json": json_bytes(sample),
        prefix + "-blank.csv": blank_csv(cleaned, sample["sample_sha256"]),
        prefix + ".html": render_review(cleaned, metadata),
    }
    return outputs, {"cases": len(cleaned), "sample_sha256": sample["sample_sha256"]}


def build(root: Path, completed_csv: Path, evidence_file: Path) -> tuple[dict[str, bytes], dict]:
    outputs, ledger = build_ledger(root, completed_csv)
    latest = json.loads(outputs[f"{DIRECTORY}/latest-decisions.json"])["decisions"]
    previous = {row["pair_id"]: row for row in latest}
    parent = check_triage(root)
    candidates = _read(root, "data/review/triage-v2/triage-pairs.json")
    pairs = {row["pair_id"]: row for row in candidates}
    records = _read(root, "data/review/triage-v2/records.json")
    units = _read(root, "data/review/triage-v2/review-units.json")
    views = _read(root, "data/review/triage-v2/source-views.json")
    source_names = _read(root, "data/review/linkage/sample.json")["source_names"]
    evidence_bytes = evidence_file.read_bytes()
    evidence, evidence_summary = reconcile_evidence(
        json.loads(evidence_bytes), latest, records, pairs
    )
    if evidence_file.read_bytes() != evidence_bytes:
        raise ValueError("Research input changed during preparation")
    outputs[f"{DIRECTORY}/source-research-input.json"] = evidence_bytes
    outputs[f"{DIRECTORY}/source-research.json"] = json_bytes(evidence)
    research = {case["pair_id"]: case for case in evidence["cases"]}
    selected, selection = select_benchmark(candidates, units, views, set(previous))
    historical = []
    for row in selected:
        known = row["historical_pair_ids"]
        if known:
            history = previous[row["pair_id"]]
            disposition = research.get(row["pair_id"], {}).get("disposition")
            row["historical_decision"] = history
            row["review_status"] = disposition or (
                "research_open"
                if history["label"]
                in {"uncertain", "related_scope_unclear", "insufficient_evidence"}
                else "prior_decision_available"
            )
            historical.append(row)
        else:
            row["review_status"] = "unreviewed"
    benchmark = {
        "schema_version": "validation-design-v1",
        "selection": selection,
        "cases": selected,
        "model_fitted": False,
        "formal_gold_set_complete": False,
        "policy": (
            "Historical answers remain available; unresolved research "
            "and adjudication are not gold labels."
        ),
    }
    benchmark["sample_sha256"] = sample_digest(benchmark)
    outputs[f"{DIRECTORY}/benchmark.json"] = json_bytes(benchmark)
    modes = {
        "adjudication": [
            _case(
                pairs[case["pair_id"]], records, candidates, views, previous[case["pair_id"]], case
            )
            for case in evidence["cases"]
            if case["disposition"] == "human_adjudication"
        ],
        "development": [
            _case(row, records, candidates, views)
            for row in selected
            if row["split"] == "development" and not row["historical_pair_ids"]
        ],
        "holdout": [
            _case(row, records, candidates, views) for row in selected if row["split"] == "holdout"
        ],
    }
    packets = {}
    for mode, cases in modes.items():
        packet_outputs, info = _packet(cases, mode, source_names)
        outputs.update(packet_outputs)
        packets[mode] = info
    readiness = dict(Counter(row["review_status"] for row in historical))
    receipt = {
        "schema_version": "reconciliation-audit-v1",
        "phase": 0,
        "step": 6,
        "status": "evidence_and_validation_prepared_human_review_pending",
        "parent_receipt_sha256": sha256(root / "reports/triage-audit.json"),
        "parent_sample_sha256": parent["sample_sha256"],
        "ledger": ledger,
        "research": evidence_summary,
        "selection": selection,
        "historical_readiness": readiness,
        "packets": packets,
        "benchmark_sha256": benchmark["sample_sha256"],
        "implementation": implementation_hashes(root),
        "model_fitted": False,
        "auto_merges": 0,
        "human_labels_created": 0,
        "canonical_rows_added": 0,
        "published_rows_added": 0,
        "formal_gold_set_complete": False,
        "privacy": (
            "Human decisions, research links tied to observations "
            "and source records remain local and Git-ignored."
        ),
        "limits": [
            (
                "Research proposals are not human decisions. "
                "Earlier human labels are never silently replaced."
            ),
            (
                "No numerical thresholds are tuned and no linkage model is fitted "
                "before the formal review gate."
            ),
            (
                "Validation is confined to the existing candidate block; "
                "blocking recall is not measured."
            ),
            (
                "Seeded diversity quotas are not a probability sample; "
                "no population-weighted accuracy is claimed."
            ),
            (
                "All three current likely-match review units were previously exposed and "
                "are development-only; that heuristic has no independent holdout test here."
            ),
            (
                "Source-linked public evidence can still leave exact record boundaries "
                "and membership unresolved."
            ),
        ],
    }
    outputs[f"{DIRECTORY}/index.html"] = render_index(receipt, evidence)
    outputs[REPORT] = render_report(receipt)
    receipt["artifacts"] = {
        name: {"sha256": digest(content), "bytes": len(content)}
        for name, content in sorted(outputs.items())
    }
    validate_receipt(receipt)
    return outputs, receipt


def validate_receipt(receipt: dict) -> None:
    if (
        receipt["schema_version"] != "reconciliation-audit-v1"
        or receipt["status"] != "evidence_and_validation_prepared_human_review_pending"
        or receipt["phase"] != 0
        or receipt["step"] != 6
        or receipt["model_fitted"] is not False
        or receipt["formal_gold_set_complete"] is not False
        or any(
            type(receipt[key]) is not int or receipt[key] != 0
            for key in (
                "auto_merges",
                "human_labels_created",
                "canonical_rows_added",
                "published_rows_added",
            )
        )
    ):
        raise ValueError("Invalid reconciliation checkpoint")
    ledger, selection, research = (receipt[key] for key in ("ledger", "selection", "research"))

    def valid_counts(values):
        return all(type(value) is int and value >= 0 for value in values)

    count_groups = [
        receipt["historical_readiness"].values(),
        research["disposition_counts"].values(),
        research["proposal_counts"].values(),
        selection["split_counts"].values(),
        [
            selection[key]
            for key in (
                "size",
                "retained_historical_units",
                "new_units",
                "historical_source_view_pairs_excluded",
            )
        ],
        [research[key] for key in ("researched_cases", "findings", "human_labels_changed")],
        [packet["cases"] for packet in receipt["packets"].values()],
    ]
    if any(not valid_counts(values) for values in count_groups):
        raise ValueError("Review aggregate counts require nonnegative integers")
    hashes = [
        receipt[key]
        for key in ("parent_receipt_sha256", "parent_sample_sha256", "benchmark_sha256")
    ]
    hashes.extend(packet["sample_sha256"] for packet in receipt["packets"].values())
    if any(not re.fullmatch(r"[0-9a-f]{64}", str(value)) for value in hashes):
        raise ValueError("Invalid reconciliation sample or parent checksum")
    if (
        ledger["history_events"] != 40
        or ledger["unique_reviewed_pairs"] != 34
        or selection["size"] != 200
        or selection["split_counts"] != {"development": 120, "holdout": 80}
        or selection["retained_historical_units"] + selection["new_units"] != 200
        or selection["historical_source_view_pairs_excluded"]
        + selection["retained_historical_units"]
        != ledger["unique_reviewed_pairs"]
        or sum(receipt["historical_readiness"].values()) != selection["retained_historical_units"]
        or sum(research["disposition_counts"].values()) != research["researched_cases"]
        or research["human_labels_changed"] != 0
    ):
        raise ValueError("Review and selection aggregates do not reconcile")
    if (
        set(receipt["packets"]) != set(PACKET_MODES)
        or receipt["packets"]["adjudication"]["cases"]
        != research["disposition_counts"].get("human_adjudication", 0)
        or receipt["packets"]["development"]["cases"]
        != 120 - selection["retained_historical_units"]
        or receipt["packets"]["holdout"]["cases"] != 80
    ):
        raise ValueError("Review packet counts do not reconcile")
    if set(receipt["artifacts"]) != {REPORT, *(f"{DIRECTORY}/{name}" for name in PRIVATE_FILES)}:
        raise ValueError("Unexpected reconciliation artifact set")
    for name, item in receipt["artifacts"].items():
        if (
            not (name.startswith(DIRECTORY + "/") or name == REPORT)
            or ".." in Path(name).parts
            or Path(name).is_absolute()
            or not re.fullmatch(r"[0-9a-f]{64}", str(item["sha256"]))
            or type(item["bytes"]) is not int
            or item["bytes"] < 1
        ):
            raise ValueError("Invalid reconciliation artifact path or checksum")


def prepare(
    root: Path, completed_csv: Path | None = None, evidence_file: Path | None = None
) -> dict:
    outputs, receipt = build(
        root,
        completed_csv or root / DIRECTORY / "completed-pilot.csv",
        evidence_file or root / DIRECTORY / "source-research-input.json",
    )
    outputs[RECEIPT] = json_bytes(receipt)
    with tempfile.TemporaryDirectory(prefix=".reconciliation-", dir=root / "data") as temporary:
        staged = []
        for name, content in outputs.items():
            path = Path(temporary) / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
            staged.append((path, root / name))
        accept_outputs(staged)
    return receipt


def check(root: Path, *, local=False) -> dict:
    parent = check_triage(root)
    receipt = _read(root, RECEIPT)
    validate_receipt(receipt)
    report = render_report(receipt)
    if (
        receipt["implementation"] != implementation_hashes(root)
        or receipt["parent_receipt_sha256"] != sha256(root / "reports/triage-audit.json")
        or receipt["parent_sample_sha256"] != parent["sample_sha256"]
        or receipt["ledger"]["parent_triage_receipt_sha256"] != receipt["parent_receipt_sha256"]
        or (root / REPORT).read_bytes() != report
        or receipt["artifacts"][REPORT] != {"sha256": digest(report), "bytes": len(report)}
    ):
        raise ValueError("Reconciliation code, parent receipt or report changed")
    if local:
        outputs, rebuilt = build(
            root,
            root / DIRECTORY / "completed-pilot.csv",
            root / DIRECTORY / "source-research-input.json",
        )
        if rebuilt != receipt:
            raise ValueError("Rebuilt reconciliation receipt changed")
        for name, content in outputs.items():
            if (root / name).read_bytes() != content:
                raise ValueError(f"Reconciliation artifact changed: {name}")
        benchmark = _read(root, f"{DIRECTORY}/benchmark.json")
        latest = _read(root, f"{DIRECTORY}/latest-decisions.json")
        validate_selection(benchmark["cases"], {r["pair_id"] for r in latest["decisions"]})
    return receipt


def load_packet(root: Path, receipt: dict, mode: str) -> dict:
    if mode not in PACKET_MODES:
        raise ValueError("Unknown validation packet")
    name = f"{DIRECTORY}/{mode}.json"
    content = (root / name).read_bytes()
    if receipt["artifacts"][name] != {"sha256": digest(content), "bytes": len(content)}:
        raise ValueError("Accepted validation packet changed")
    packet = json.loads(content)
    if (
        packet.get("mode") != mode
        or packet.get("sample_sha256") != receipt["packets"][mode]["sample_sha256"]
        or sample_digest(packet) != packet.get("sample_sha256")
        or len(packet.get("cases", [])) != receipt["packets"][mode]["cases"]
    ):
        raise ValueError("Validation packet does not match the accepted design")
    return packet


def validate_export(path: Path, sample: dict, *, complete=False) -> dict:
    if (
        sample.get("schema_version") != "validation-review-v1"
        or sample.get("mode") not in PACKET_MODES
        or sample_digest(sample) != sample.get("sample_sha256")
    ):
        raise ValueError("Invalid or changed validation packet")
    if not isinstance(sample.get("cases"), list) or not 1 <= len(sample["cases"]) <= 200:
        raise ValueError("Validation sample must contain 1–200 cases")
    identities = set()
    for case in sample["cases"]:
        if any(
            not isinstance(case.get(key), str) or not case[key].strip()
            for key in ("pair_id", "left_record_id", "right_record_id")
        ):
            raise ValueError("Invalid validation case IDs")
        identity = tuple(sorted((case["left_record_id"], case["right_record_id"])))
        if identity[0] == identity[1] or identity in identities:
            raise ValueError("Duplicate or self-paired validation records")
        identities.add(identity)
    expected = {case["pair_id"]: case for case in sample["cases"]}
    if len(expected) != len(sample["cases"]):
        raise ValueError("Duplicate sample identities")
    raw = path.read_bytes()
    reader = csv.DictReader(io.StringIO(raw.decode("utf-8-sig"), newline=""), strict=True)
    headers = [
        "pair_id",
        "left_record_id",
        "right_record_id",
        "label",
        "reviewer",
        "notes",
        "sample_sha256",
    ]
    if reader.fieldnames != headers:
        raise ValueError("Unexpected review CSV columns")
    seen, counts = set(), Counter()
    for row in reader:
        identity = row["pair_id"]
        if (
            set(row) != set(headers)
            or any(not isinstance(value, str) for value in row.values())
            or identity not in expected
            or identity in seen
            or row["sample_sha256"] != sample["sample_sha256"]
            or row["label"] not in LABELS
            or any(row[key] != expected[identity][key] for key in headers[1:3])
            or (row["label"] and not row["reviewer"].strip())
        ):
            raise ValueError("Unknown, duplicate, altered or malformed review decision")
        seen.add(identity)
        counts[row["label"] or "blank"] += 1
    if seen != set(expected) or (complete and counts["blank"]):
        raise ValueError("Review export is missing cases or required answers")
    if path.read_bytes() != raw:
        raise ValueError("Review export changed during validation")
    return {
        "mode": sample["mode"],
        "cases": len(expected),
        "counts": dict(counts),
        "all_cases_reviewed": counts["blank"] == 0,
        "formal_gold_set_complete": False,
    }
