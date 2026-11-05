"""Append human approval without changing past decisions or the frozen experiment."""

from __future__ import annotations

import copy
import csv
import io
import json
import re
import tempfile
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path

from pipeline.final_review.core import DIRECTORY as RESEARCH_DIRECTORY
from pipeline.final_review.core import RECEIPT as RESEARCH_RECEIPT
from pipeline.final_review.core import check as check_research
from pipeline.link.labels import HEADERS
from pipeline.normalize.core import accept_outputs, sha256
from pipeline.reconciliation.core import validate_export
from pipeline.reconciliation.ledger import RELATIONSHIPS, UNRESOLVED
from pipeline.review_acceptance.core import bound, completed_csv
from pipeline.review_rounds.core import DIRECTORY as PREVIOUS_DIRECTORY
from pipeline.review_rounds.core import RECEIPT as PREVIOUS_RECEIPT
from pipeline.scaffold import digest, json_bytes
from pipeline.triage.labels import LABELS, sample_digest

DIRECTORY = "data/review/final47-approved-v1"
RECEIPT = "reports/final47-accepted-audit.json"
REPORT = "reports/final47-accepted.md"
ROUND = "researched-final47-chat-approval"
BENCHMARK_RECEIPT = "reports/reconciliation-audit.json"
BENCHMARK = "data/review/reconciliation-v1/benchmark.json"
EXPERIMENT_RECEIPT = "reports/matching-experiment-audit.json"
PARENT_HISTORY = f"{PREVIOUS_DIRECTORY}/decision-history.json"
PARENT_LATEST = f"{PREVIOUS_DIRECTORY}/latest-decisions.json"
PRIVATE_NAMES = ("approval.json", "completed.csv", "decision-history.json", "latest-decisions.json")
MESSAGE = "I approve all of the agents reviews. How should we proceed here?"
APPROVED_ON = "2026-11-04"
TIMEZONE = "America/Los_Angeles"
REVIEWER = "Raj Sawhney"
ASSISTANCE = "agent_research_and_second_review"
ROLE = "agent_assisted_audit_not_blind"
CAPTURE = "explicit_user_chat_approval"
NEW_LABELS = LABELS - {""}
HISTORY_LABELS = NEW_LABELS | {"uncertain"}


def implementation_hashes(root: Path) -> dict:
    return {
        p.relative_to(root).as_posix(): sha256(p)
        for p in [*sorted((root / "pipeline/final_acceptance").glob("*.py")), root / "uv.lock"]
    }


def _hash(value) -> bool:
    return isinstance(value, str) and re.fullmatch(r"[a-f0-9]{64}", value) is not None


def _counts(value, allowed=HISTORY_LABELS) -> bool:
    return (
        isinstance(value, dict)
        and not set(value) - allowed
        and all(type(n) is int and n > 0 for n in value.values())
    )


def validate_approval(approval: dict, sample: dict) -> None:
    cases = sample.get("cases")
    if (
        sample.get("schema_version") != "validation-review-v1"
        or sample.get("mode") != "adjudication"
        or sample.get("original_split") != "holdout"
        or sample.get("evaluation_role") != ROLE
        or not _hash(sample.get("sample_sha256"))
        or sample_digest(sample) != sample["sample_sha256"]
        or not _hash(sample.get("research_sha256"))
        or not isinstance(cases, list)
        or len(cases) != 47
    ):
        raise ValueError("Invalid final47 assisted sample")
    expected, identities = {}, set()
    for case in cases:
        if (
            not isinstance(case, dict)
            or any(not isinstance(case.get(k), str) or not case[k].strip() for k in HEADERS[:3])
            or not isinstance(case.get("research"), dict)
            or case["research"].get("recommendation") not in NEW_LABELS
        ):
            raise ValueError("Invalid final47 case identity or recommendation")
        identity = tuple(sorted((case["left_record_id"], case["right_record_id"])))
        if identity[0] == identity[1] or identity in identities or case["pair_id"] in expected:
            raise ValueError("Duplicate or self-paired final47 case")
        identities.add(identity)
        expected[case["pair_id"]] = case["research"]["recommendation"]
    try:
        valid_date = date.fromisoformat(approval.get("approved_on", "")).isoformat()
    except (TypeError, ValueError) as exc:
        raise ValueError("Approval requires a valid calendar date") from exc
    metadata = {
        "schema_version": "explicit-chat-approval-v1",
        "capture_method": CAPTURE,
        "batch_id": "final47-v1",
        "sample_sha256": sample["sample_sha256"],
        "research_sha256": sample["research_sha256"],
        "reviewer": REVIEWER,
        "approved_on": APPROVED_ON,
        "user_message": MESSAGE,
        "assistance": ASSISTANCE,
        "approved_labels": expected,
    }
    if approval != metadata or valid_date != APPROVED_ON:
        raise ValueError("Approval must bind the exact chat statement and all 47 final labels")


def latest_view(history: dict) -> dict:
    by_pair = defaultdict(list)
    event_ids, identities = set(), {}
    for event in history["events"]:
        if (
            any(not isinstance(event.get(k), str) for k in HEADERS)
            or any(not event[k].strip() for k in (*HEADERS[:3], "reviewer"))
            or event["label"] not in HISTORY_LABELS
            or not isinstance(event.get("event_id"), str)
            or not event["event_id"]
            or event["event_id"] in event_ids
            or not isinstance(event.get("round_id"), str)
        ):
            raise ValueError("Invalid decision history event")
        event_ids.add(event["event_id"])
        identity = (event["left_record_id"], event["right_record_id"])
        unordered = tuple(sorted(identity))
        if (
            identity[0] == identity[1]
            or identities.setdefault(unordered, event["pair_id"]) != event["pair_id"]
        ):
            raise ValueError("History contains a duplicate record pair or self-pair")
        prior = by_pair[event["pair_id"]]
        if prior and identity != (prior[0]["left_record_id"], prior[0]["right_record_id"]):
            raise ValueError("History pair orientation changed")
        prior.append(event)
    decisions = []
    for _, events in sorted(by_pair.items()):
        newest = events[-1]
        decisions.append(
            {
                **{key: newest[key] for key in HEADERS},
                "round_id": newest["round_id"],
                "latest_event_id": newest["event_id"],
                "history_event_ids": [e["event_id"] for e in events],
            }
        )
    return {
        "schema_version": "latest-human-decisions-v1",
        "history_sha256": digest(json_bytes(history)),
        "decisions": decisions,
        "counts": dict(sorted(Counter(r["label"] for r in decisions).items())),
        "policy": (
            "Explicit human decisions; assisted approvals retain their provenance. "
            "Unresolved and containment answers remain separate from identity. No automatic merges."
        ),
    }


def append_approval(
    history: dict, rows: list, approval: dict, sample: dict, csv_hash: str, approval_hash: str
) -> tuple:
    validate_approval(approval, sample)
    if (
        history.get("schema_version") != "decision-history-v1"
        or len(history.get("events", [])) != 167
        or not isinstance(history.get("rounds"), list)
        or any(r.get("round_id") == ROUND for r in history["rounds"])
        or not _hash(csv_hash)
        or not _hash(approval_hash)
    ):
        raise ValueError("Approval requires the frozen pre-approval history")
    prior = latest_view(history)
    expected_rows = list(csv.DictReader(io.StringIO(completed_csv(approval, sample).decode())))
    old_ids = {r["pair_id"] for r in prior["decisions"]}
    if len(old_ids) != 154 or rows != expected_rows or old_ids & set(approval["approved_labels"]):
        raise ValueError("Approval may only append the 47 exact new approved decisions")
    updated = copy.deepcopy(history)
    for row in rows:
        event = {
            **row,
            "round_id": ROUND,
            "source_csv_sha256": csv_hash,
            "approval_sha256": approval_hash,
            "capture_method": CAPTURE,
            "assistance": ASSISTANCE,
            "approved_on": APPROVED_ON,
            "approved_timezone": TIMEZONE,
            "original_split": "holdout",
            "evaluation_role": ROLE,
        }
        event["event_id"] = "decision_" + digest(json_bytes(event))
        updated["events"].append(event)
    updated["rounds"].append(
        {
            "round_id": ROUND,
            "source_csv_sha256": csv_hash,
            "approval_sha256": approval_hash,
            "capture_method": CAPTURE,
            "assistance": ASSISTANCE,
            "evaluation_role": ROLE,
            "event_count": 47,
        }
    )
    return updated, latest_view(updated)


def formal_summary(benchmark: dict, latest: dict, sample: dict, history: dict) -> dict:
    cases = benchmark.get("cases", [])
    if (
        benchmark.get("schema_version") != "validation-design-v1"
        or sample_digest(benchmark) != benchmark.get("sample_sha256")
        or len(cases) != 200
        or len({c["pair_id"] for c in cases}) != 200
        or Counter(c["split"] for c in cases) != {"development": 120, "holdout": 80}
    ):
        raise ValueError("Invalid frozen formal benchmark")
    by_pair = {r["pair_id"]: r for r in latest["decisions"]}
    formal_ids = {c["pair_id"] for c in cases}
    if not formal_ids <= by_pair.keys():
        raise ValueError("Formal benchmark still has missing human answers")
    for case in cases:
        if any(case[key] != by_pair[case["pair_id"]][key] for key in HEADERS[1:3]):
            raise ValueError("Formal benchmark identity or orientation changed")
    holdout = [c["pair_id"] for c in cases if c["split"] == "holdout"]
    if [c["pair_id"] for c in sample["cases"]] != holdout[33:]:
        raise ValueError("Final approval must preserve the original final47 selection")
    blind = [e for e in history["events"] if e["round_id"] == "next60-completed-v1-holdout"]
    if (
        len(blind) != 33
        or {e["pair_id"] for e in blind} != set(holdout[:33])
        or any(
            e.get("assistance") != "blind_source_only"
            or e.get("capture_method") != "user_submitted_browser_export"
            for e in blind
        )
    ):
        raise ValueError("Original 33 blind collection events must remain intact")
    formal = Counter(by_pair[p]["label"] for p in formal_ids)
    nonformal = Counter(r["label"] for p, r in by_pair.items() if p not in formal_ids)
    return {
        "formal_human_reviewed": len(formal_ids),
        "formal_counts": dict(sorted(formal.items())),
        "formal_binary_labeled": sum(formal[k] for k in ("same_site", "different_sites")),
        "formal_containment": sum(formal[k] for k in RELATIONSHIPS),
        "formal_unresolved": sum(formal[k] for k in UNRESOLVED),
        "nonformal_latest_counts": dict(sorted(nonformal.items())),
        "blind_collection_cases_preserved": len(blind),
    }


def render_report(receipt: dict) -> bytes:
    r = receipt["review"]
    rows = "\n".join(f"| {label} | {count} |" for label, count in r["approved_counts"].items())
    return f"""# Final 47 researched decisions accepted

Raj explicitly approved all final agent recommendations in chat on {receipt["approved_on"]}
({TIMEZONE}). The private approval preserves the exact statement, reviewer, final sample
and research checksums, and all approved pair labels. Its completed CSV is an administrative
transcription of chat approval, not a browser submission or blind annotation.

| Accepted relationship | Cases |
| --- | ---: |
{rows}

All 167 previous events are preserved unchanged. The 47 new decisions bring the history
to {r["history_events"]} events across {r["unique_comparisons"]} comparisons. The latest
view retains {r["latest_unresolved"]} unresolved relationships. They remain abstentions;
containment remains separate from same-site identity. No canonical merges occur.

All {r["formal_human_reviewed"]} units in the receipt-bound formal benchmark now have human
answers: {r["formal_binary_labeled"]} binary same/different labels,
{r["formal_containment"]} containment relationships and {r["formal_unresolved"]} unresolved
answers. One historical publisher-view comparison remains outside that benchmark.
**Human review is complete; a complete binary gold set is not.** This acceptance does not
establish a 97% quality gate, precision/recall claim, or permission to merge uncertain sites.

The 33 earlier holdout answers retain their blind-at-collection provenance. These final
47 are agent-assisted approvals, not additional blind validation. The frozen matching
experiment and its original evaluation remain unchanged; this step fits or retunes no model.

Private files are in `{DIRECTORY}/`. The latest view there supersedes earlier views for
future review work. Original receipts remain immutable historical snapshots. Offline CI
checks this aggregate receipt and parent hashes; local checking rebuilds every private byte.
""".encode()


def build(root: Path, approval_path: Path) -> tuple[dict, dict]:
    parent = check_research(root, local=True)
    previous = json.loads((root / PREVIOUS_RECEIPT).read_text())
    design = json.loads((root / BENCHMARK_RECEIPT).read_text())
    history = json.loads(bound(root, previous, PARENT_HISTORY))
    old_latest = json.loads(bound(root, previous, PARENT_LATEST))
    derived = latest_view(history)
    if any(
        old_latest.get(k) != derived[k]
        for k in ("schema_version", "history_sha256", "decisions", "counts")
    ):
        raise ValueError("Parent latest view does not preserve parent history")
    sample = json.loads(bound(root, parent, f"{RESEARCH_DIRECTORY}/sample.json"))
    research_hash = parent["artifacts"][f"{RESEARCH_DIRECTORY}/research-input.json"]["sha256"]
    if (
        sample["sample_sha256"] != parent["sample_sha256"]
        or sample["research_sha256"] != research_hash
    ):
        raise ValueError("Final sample and research do not match their parent receipt")
    benchmark = json.loads(bound(root, design, BENCHMARK))
    if benchmark["sample_sha256"] != design["benchmark_sha256"]:
        raise ValueError("Formal benchmark does not match its receipt")
    raw = approval_path.read_bytes()
    approval = json.loads(raw)
    validate_approval(approval, sample)
    content = completed_csv(approval, sample)
    with tempfile.TemporaryDirectory(prefix="atlas-final-approval-check-") as temporary:
        path = Path(temporary) / "completed.csv"
        path.write_bytes(content)
        validate_export(path, sample, complete=True)
    rows = list(csv.DictReader(io.StringIO(content.decode())))
    updated, latest = append_approval(history, rows, approval, sample, digest(content), digest(raw))
    review = {
        "previous_events_preserved": len(history["events"]),
        "new_approved_decisions": len(rows),
        "history_events": len(updated["events"]),
        "unique_comparisons": len(latest["decisions"]),
        "approved_counts": dict(sorted(Counter(r["label"] for r in rows).items())),
        "latest_counts": latest["counts"],
        "latest_unresolved": sum(r["label"] in UNRESOLVED for r in latest["decisions"]),
        "pending_human_review": 0,
        "new_blind_cases": 0,
        **formal_summary(benchmark, latest, sample, updated),
    }
    outputs = {
        f"{DIRECTORY}/approval.json": raw,
        f"{DIRECTORY}/completed.csv": content,
        f"{DIRECTORY}/decision-history.json": json_bytes(updated),
        f"{DIRECTORY}/latest-decisions.json": json_bytes(latest),
    }
    receipt = {
        "schema_version": "final47-accepted-v1",
        "status": "explicit_human_approval_recorded",
        "approved_on": APPROVED_ON,
        "approved_timezone": TIMEZONE,
        "approval": {
            "reviewer": REVIEWER,
            "capture_method": CAPTURE,
            "assistance": ASSISTANCE,
            "evaluation_role": ROLE,
            "user_message_sha256": digest(MESSAGE.encode()),
            "approved_labels_sha256": digest(json_bytes(approval["approved_labels"])),
        },
        "approval_sha256": digest(raw),
        "parent_receipt_sha256": sha256(root / RESEARCH_RECEIPT),
        "previous_receipt_sha256": sha256(root / PREVIOUS_RECEIPT),
        "benchmark_receipt_sha256": sha256(root / BENCHMARK_RECEIPT),
        "frozen_experiment_receipt_sha256": sha256(root / EXPERIMENT_RECEIPT),
        "benchmark_sha256": benchmark["sample_sha256"],
        "sample_sha256": sample["sample_sha256"],
        "research_sha256": sample["research_sha256"],
        "review": review,
        "formal_review_complete": True,
        "formal_binary_gold_complete": False,
        "model_fitted_by_acceptance": False,
        "auto_merges": 0,
        "implementation": implementation_hashes(root),
    }
    outputs[REPORT] = render_report(receipt)
    receipt["artifacts"] = {
        n: {"sha256": digest(v), "bytes": len(v)} for n, v in sorted(outputs.items())
    }
    if approval_path.read_bytes() != raw:
        raise ValueError("Approval changed during acceptance")
    validate_receipt(receipt, parent, previous, design)
    return outputs, receipt


def validate_receipt(receipt: dict, parent: dict, previous: dict, design: dict) -> None:
    r = receipt.get("review")
    if not isinstance(r, dict):
        raise ValueError("Invalid final acceptance review counts")
    count_names = ("approved_counts", "latest_counts", "formal_counts", "nonformal_latest_counts")
    if any(
        not _counts(r.get(k), NEW_LABELS if k == "approved_counts" else HISTORY_LABELS)
        for k in count_names
    ):
        raise ValueError("Invalid final acceptance label counts")
    scalar_names = (
        "previous_events_preserved",
        "new_approved_decisions",
        "history_events",
        "unique_comparisons",
        "latest_unresolved",
        "pending_human_review",
        "new_blind_cases",
        "formal_human_reviewed",
        "formal_binary_labeled",
        "formal_containment",
        "formal_unresolved",
        "blind_collection_cases_preserved",
    )
    if any(type(r.get(k)) is not int or r[k] < 0 for k in scalar_names):
        raise ValueError("Final acceptance counts must be nonnegative integers")
    expected_latest = Counter(previous["review"]["latest_counts"]) + Counter(r["approved_counts"])
    expected_formal = Counter(r["latest_counts"]) - Counter(r["nonformal_latest_counts"])
    a = receipt.get("approval")
    expected_attestation = {
        "reviewer": REVIEWER,
        "capture_method": CAPTURE,
        "assistance": ASSISTANCE,
        "evaluation_role": ROLE,
        "user_message_sha256": digest(MESSAGE.encode()),
    }
    if (
        not isinstance(a, dict)
        or set(a) != {*expected_attestation, "approved_labels_sha256"}
        or any(a.get(k) != v for k, v in expected_attestation.items())
        or not _hash(a.get("approved_labels_sha256"))
    ):
        raise ValueError("Invalid final acceptance approval provenance")
    if (
        receipt.get("schema_version") != "final47-accepted-v1"
        or receipt.get("status") != "explicit_human_approval_recorded"
        or receipt.get("approved_on") != APPROVED_ON
        or receipt.get("approved_timezone") != TIMEZONE
        or receipt.get("formal_review_complete") is not True
        or receipt.get("formal_binary_gold_complete") is not False
        or receipt.get("model_fitted_by_acceptance") is not False
        or type(receipt.get("auto_merges")) is not int
        or receipt["auto_merges"] != 0
        or r["previous_events_preserved"] != 167
        or r["new_approved_decisions"] != 47
        or r["history_events"] != 214
        or r["unique_comparisons"] != 201
        or r["approved_counts"] != parent["research"]["recommendation_counts"]
        or r["latest_counts"] != expected_latest
        or sum(r["latest_counts"].values()) != 201
        or r["latest_unresolved"] != sum(r["latest_counts"].get(k, 0) for k in UNRESOLVED)
        or r["latest_unresolved"] != 73
        or r["pending_human_review"] != 0
        or r["new_blind_cases"] != 0
        or r["blind_collection_cases_preserved"] != 33
        or r["formal_human_reviewed"] != 200
        or design["selection"]["size"] != 200
        or r["nonformal_latest_counts"] != {"same_site": 1}
        or r["formal_counts"] != expected_formal
        or sum(r["formal_counts"].values()) != 200
        or r["formal_binary_labeled"]
        != sum(r["formal_counts"].get(k, 0) for k in ("same_site", "different_sites"))
        or r["formal_containment"] != sum(r["formal_counts"].get(k, 0) for k in RELATIONSHIPS)
        or r["formal_unresolved"] != sum(r["formal_counts"].get(k, 0) for k in UNRESOLVED)
        or r["formal_unresolved"] != 73
        or receipt.get("sample_sha256") != parent["sample_sha256"]
        or receipt.get("research_sha256")
        != parent["artifacts"][f"{RESEARCH_DIRECTORY}/research-input.json"]["sha256"]
        or receipt.get("benchmark_sha256") != design["benchmark_sha256"]
        or not isinstance(receipt.get("artifacts"), dict)
        or set(receipt["artifacts"]) != {REPORT, *(f"{DIRECTORY}/{n}" for n in PRIVATE_NAMES)}
    ):
        raise ValueError("Invalid final acceptance aggregate receipt")
    hashes = [
        receipt.get(k)
        for k in (
            "approval_sha256",
            "parent_receipt_sha256",
            "previous_receipt_sha256",
            "benchmark_receipt_sha256",
            "frozen_experiment_receipt_sha256",
            "benchmark_sha256",
            "sample_sha256",
            "research_sha256",
        )
    ]
    implementation = receipt.get("implementation")
    if not isinstance(implementation, dict) or not implementation:
        raise ValueError("Invalid acceptance implementation checksums")
    hashes.extend(implementation.values())
    for entry in receipt["artifacts"].values():
        if (
            not isinstance(entry, dict)
            or set(entry) != {"sha256", "bytes"}
            or type(entry["bytes"]) is not int
            or entry["bytes"] < 1
        ):
            raise ValueError("Invalid acceptance artifact size or checksum")
        hashes.append(entry["sha256"])
    if any(not _hash(v) for v in hashes):
        raise ValueError("Invalid final acceptance checksum")
    if receipt["approval_sha256"] != receipt["artifacts"][f"{DIRECTORY}/approval.json"]["sha256"]:
        raise ValueError("Approval checksum is not bound to its preserved artifact")


def prepare(root: Path, approval_path: Path | None = None) -> dict:
    outputs, receipt = build(root, approval_path or root / DIRECTORY / "approval.json")
    outputs[RECEIPT] = json_bytes(receipt)
    with tempfile.TemporaryDirectory(prefix=".final-approval-", dir=root / "data") as temporary:
        staged = []
        for name, content in outputs.items():
            path = Path(temporary) / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
            staged.append((path, root / name))
        accept_outputs(staged)
    return receipt


def check(root: Path, *, local=False) -> dict:
    parent = check_research(root)
    previous = json.loads((root / PREVIOUS_RECEIPT).read_text())
    design = json.loads((root / BENCHMARK_RECEIPT).read_text())
    receipt = json.loads((root / RECEIPT).read_text())
    validate_receipt(receipt, parent, previous, design)
    if (
        receipt["implementation"] != implementation_hashes(root)
        or receipt["parent_receipt_sha256"] != sha256(root / RESEARCH_RECEIPT)
        or receipt["previous_receipt_sha256"] != sha256(root / PREVIOUS_RECEIPT)
        or receipt["benchmark_receipt_sha256"] != sha256(root / BENCHMARK_RECEIPT)
        or receipt["frozen_experiment_receipt_sha256"] != sha256(root / EXPERIMENT_RECEIPT)
        or bound(root, receipt, REPORT) != render_report(receipt)
    ):
        raise ValueError("Final acceptance code, parents, experiment or report changed")
    if local:
        outputs, rebuilt = build(root, root / DIRECTORY / "approval.json")
        if rebuilt != receipt or any((root / n).read_bytes() != v for n, v in outputs.items()):
            raise ValueError("Final acceptance rebuild changed")
    return receipt
