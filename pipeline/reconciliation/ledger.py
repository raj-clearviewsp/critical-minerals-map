"""Preserve both human-review rounds and derive an explicitly versioned latest view."""

from __future__ import annotations

import hashlib
import json
from collections import Counter, defaultdict
from pathlib import Path

from pipeline.link.labels import HEADERS
from pipeline.scaffold import digest, json_bytes
from pipeline.triage.core import check as check_triage
from pipeline.triage.labels import load_bound_sample, preserve_import, validate_pilot_labels

DIRECTORY = "data/review/reconciliation-v1"
TRIAGE_RECEIPT = "reports/triage-audit.json"
ORIGINAL_RECEIPT = "reports/linkage-review-audit.json"
ORIGINAL_SAMPLE = "data/review/linkage/sample.json"
ORIGINAL_CSV = "data/review/triage-v2/original-decisions.csv"
PRESERVED = "data/review/triage-v2/preserved-decisions.json"
UNRESOLVED = {"uncertain", "insufficient_evidence", "related_scope_unclear"}
RELATIONSHIPS = {"left_contains_right", "right_contains_left"}
QUEUES = {"likely_match", "likely_distinct", "relationship", "ambiguous"}


class LedgerError(ValueError):
    """Human-review identities or immutable source artifacts changed."""


def _bound_bytes(root: Path, receipt: dict, relative: str) -> bytes:
    value = (root / relative).read_bytes()
    if receipt["artifacts"][relative] != {"bytes": len(value), "sha256": digest(value)}:
        raise LedgerError(f"Accepted parent artifact changed: {relative}")
    return value


def _verify_artifacts(root: Path, receipt: dict) -> None:
    for relative, expected in receipt["artifacts"].items():
        path = root / relative
        with path.open("rb") as stream:
            actual = hashlib.file_digest(stream, "sha256").hexdigest()
        if actual != expected["sha256"] or path.stat().st_size != expected["bytes"]:
            raise LedgerError(f"Accepted parent artifact changed: {relative}")


def _event(row: dict, round_id: str, source_hash: str) -> dict:
    if set(row) != set(HEADERS) or any(not isinstance(row[field], str) for field in HEADERS):
        raise LedgerError("A history decision must preserve the exact original CSV columns")
    event = {**row, "round_id": round_id, "source_csv_sha256": source_hash}
    event["event_id"] = "decision_" + digest(json_bytes(event))
    return event


def derive_ledger(
    original: list[dict],
    completed: list[dict],
    cases: list[dict],
    *,
    original_csv_sha256: str,
    completed_csv_sha256: str,
) -> tuple[dict, dict, dict]:
    """Combine validated rounds without converting relationships or unresolved labels."""
    case_by_id = {case["pair_id"]: case for case in cases}
    if len(case_by_id) != len(cases) or len(completed) != len(cases):
        raise LedgerError("Pilot cases and completed decisions must be one-to-one")
    events, events_by_pair, identity_to_pair = [], defaultdict(list), {}
    rounds = []
    for round_id, rows, source_hash in (
        ("original-review", original, original_csv_sha256),
        ("triage-pilot", completed, completed_csv_sha256),
    ):
        seen = set()
        for row in rows:
            pair_id = row["pair_id"]
            if pair_id in seen or not row["label"]:
                raise LedgerError("A review round requires unique nonblank human decisions")
            seen.add(pair_id)
            identity = (row["left_record_id"], row["right_record_id"])
            if identity[0] == identity[1]:
                raise LedgerError("A human decision cannot identify a self-pair")
            unordered = tuple(sorted(identity))
            if identity_to_pair.setdefault(unordered, pair_id) != pair_id:
                raise LedgerError("The same record comparison has different pair IDs")
            previous = events_by_pair[pair_id]
            if previous and identity != (
                previous[0]["left_record_id"],
                previous[0]["right_record_id"],
            ):
                raise LedgerError("Record identity or orientation changed between review rounds")
            if round_id == "triage-pilot":
                case = case_by_id.get(pair_id)
                if case is None or any(row[key] != case[key] for key in HEADERS[1:3]):
                    raise LedgerError("Pilot decision identity does not match its case")
                if case["queue"] not in QUEUES:
                    raise LedgerError("Pilot case has an unknown triage queue")
                old = case.get("previous_decision")
                expected_old = {key: previous[0][key] for key in HEADERS} if previous else None
                if old != expected_old:
                    raise LedgerError("Pilot's prior decision differs from preserved history")
            event = _event(row, round_id, source_hash)
            events.append(event)
            previous.append(event)
        rounds.append(
            {
                "round_id": round_id,
                "source_csv_sha256": source_hash,
                "event_count": len(rows),
            }
        )
    if set(case_by_id) != {row["pair_id"] for row in completed}:
        raise LedgerError("Completed decisions do not cover the accepted pilot")
    history = {
        "schema_version": "decision-history-v1",
        "rounds": rounds,
        "events": events,
        "policy": "Append-only human history; event labels and original CSV fields are unchanged.",
    }
    decisions = []
    for _pair_id, pair_events in sorted(events_by_pair.items()):
        newest = pair_events[-1]
        decisions.append(
            {
                **{key: newest[key] for key in HEADERS},
                "round_id": newest["round_id"],
                "latest_event_id": newest["event_id"],
                "history_event_ids": [event["event_id"] for event in pair_events],
            }
        )
    latest = {
        "schema_version": "latest-human-decisions-v1",
        "history_sha256": digest(json_bytes(history)),
        "decisions": decisions,
        "counts": dict(sorted(Counter(row["label"] for row in decisions).items())),
        "policy": (
            "Derived latest-answer view by exact ordered pair. This is not canonical truth, "
            "an automatic merge instruction, or an independently validated gold set. "
            "All earlier answers remain in decision-history.json."
        ),
    }
    queue_by_label = defaultdict(Counter)
    case_rows, prior_comparisons, disagreements, unresolved = [], [], [], []
    by_completed = {row["pair_id"]: row for row in completed}
    for case in cases:
        row = by_completed[case["pair_id"]]
        queue, label = case["queue"], row["label"]
        queue_by_label[queue][label] += 1
        prior = events_by_pair[row["pair_id"]]
        if len(prior) > 1:
            prior_comparisons.append(
                {
                    "pair_id": row["pair_id"],
                    "original_label": prior[0]["label"],
                    "pilot_label": label,
                    "label_changed": prior[0]["label"] != label,
                    "original_event_id": prior[0]["event_id"],
                    "pilot_event_id": prior[-1]["event_id"],
                }
            )
        conflict = (queue == "likely_distinct" and label == "same_site") or (
            queue == "likely_match" and label in {"different_sites", *RELATIONSHIPS}
        )
        if conflict:
            disagreements.append(row["pair_id"])
        if label in UNRESOLVED:
            unresolved.append(row["pair_id"])
        case_rows.append(
            {
                **{key: row[key] for key in HEADERS[:3]},
                "queue": queue,
                "human_label": label,
                "event_id": prior[-1]["event_id"],
                "rule_disagreement": conflict,
                "unresolved": label in UNRESOLVED,
            }
        )
    outcomes = {
        "schema_version": "pilot-outcomes-v1",
        "counts": dict(sorted(Counter(row["label"] for row in completed).items())),
        "queue_by_label": {
            key: dict(sorted(value.items())) for key, value in sorted(queue_by_label.items())
        },
        "prior_comparisons": prior_comparisons,
        "unresolved_pair_ids": unresolved,
        "rule_disagreement_pair_ids": disagreements,
        "case_rows": case_rows,
        "formal_gold_set_complete": False,
        "accuracy_metrics_reported": False,
        "interpretation": (
            "Targeted pilot agreement diagnoses evidence/routing problems; it does not estimate "
            "population precision or recall. Human decisions remain subject to source adjudication."
        ),
    }
    return history, latest, outcomes


def build_ledger(root: Path, csv_path: Path) -> tuple[dict[str, bytes], dict]:
    """Build private artifacts and an aggregate-only summary; never write or fit a model."""
    root, csv_path = root.resolve(), csv_path.resolve()
    parent_bytes = (root / TRIAGE_RECEIPT).read_bytes()
    parent = check_triage(root)
    if json.loads(parent_bytes) != parent:
        raise LedgerError("Parent receipt changed during validation")
    _verify_artifacts(root, parent)
    original_parent = json.loads((root / ORIGINAL_RECEIPT).read_bytes())
    if digest((root / ORIGINAL_RECEIPT).read_bytes()) != parent["original_review_audit_sha256"]:
        raise LedgerError("Original receipt changed")
    original_sample = json.loads(_bound_bytes(root, original_parent, ORIGINAL_SAMPLE))
    old_bytes, old = preserve_import(root / ORIGINAL_CSV, original_sample)
    if old_bytes != _bound_bytes(root, parent, ORIGINAL_CSV) or old != json.loads(
        _bound_bytes(root, parent, PRESERVED)
    ):
        raise LedgerError("Original review history no longer matches its preserved CSV")
    sample = load_bound_sample(root, parent)
    completed_bytes = csv_path.read_bytes()
    completed = validate_pilot_labels(csv_path, sample, require_complete=True)
    if csv_path.read_bytes() != completed_bytes:
        raise LedgerError("Completed review export changed during validation")
    history, latest, outcomes = derive_ledger(
        old["decisions"],
        completed["rows"],
        sample["cases"],
        original_csv_sha256=digest(old_bytes),
        completed_csv_sha256=digest(completed_bytes),
    )
    outcomes["sample_sha256"] = sample["sample_sha256"]
    outputs = {
        f"{DIRECTORY}/completed-pilot.csv": completed_bytes,
        f"{DIRECTORY}/decision-history.json": json_bytes(history),
        f"{DIRECTORY}/latest-decisions.json": json_bytes(latest),
        f"{DIRECTORY}/pilot-outcomes.json": json_bytes(outcomes),
    }
    changed = sum(row["label_changed"] for row in outcomes["prior_comparisons"])
    summary = {
        "schema_version": "review-ledger-summary-v1",
        "parent_triage_receipt_sha256": digest(parent_bytes),
        "original_csv_sha256": digest(old_bytes),
        "completed_csv_sha256": digest(completed_bytes),
        "pilot_sample_sha256": sample["sample_sha256"],
        "original_decisions": len(old["decisions"]),
        "pilot_decisions": completed["reviewed"],
        "history_events": len(history["events"]),
        "unique_reviewed_pairs": len(latest["decisions"]),
        "repeated_pairs": len(outcomes["prior_comparisons"]),
        "changed_labels": changed,
        "unchanged_repeated_labels": len(outcomes["prior_comparisons"]) - changed,
        "pilot_label_counts": outcomes["counts"],
        "pilot_queue_by_label": outcomes["queue_by_label"],
        "latest_label_counts": latest["counts"],
        "pilot_unresolved": len(outcomes["unresolved_pair_ids"]),
        "latest_unresolved": sum(row["label"] in UNRESOLVED for row in latest["decisions"]),
        "rule_disagreements": len(outcomes["rule_disagreement_pair_ids"]),
        "formal_gold_set_complete": False,
        "accuracy_metrics_reported": False,
        "model_fitted": False,
        "auto_merges": 0,
        "interpretation": outcomes["interpretation"],
        "artifacts": {
            name: {"sha256": digest(value), "bytes": len(value)}
            for name, value in sorted(outputs.items())
        },
    }
    if (root / TRIAGE_RECEIPT).read_bytes() != parent_bytes:
        raise LedgerError("Parent receipt changed while the ledger was built")
    return outputs, summary
