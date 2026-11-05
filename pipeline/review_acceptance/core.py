"""Capture an explicit chat approval without representing it as a browser CSV export."""

from __future__ import annotations

import copy
import csv
import io
import json
import re
import tempfile
from collections import Counter, defaultdict
from pathlib import Path

from pipeline.link.labels import HEADERS
from pipeline.normalize.core import accept_outputs, sha256
from pipeline.reconciliation.core import validate_export
from pipeline.reconciliation.ledger import UNRESOLVED
from pipeline.research_review.core import DIRECTORY as RESEARCH_DIRECTORY
from pipeline.research_review.core import RECEIPT as RESEARCH_RECEIPT
from pipeline.research_review.core import check as check_research
from pipeline.scaffold import digest, json_bytes

DIRECTORY = "data/review/development60-approved-v1"
RECEIPT = "reports/development60-accepted-audit.json"
REPORT = "reports/development60-accepted.md"
ROUND = "researched-development60-chat-approval"
PARENT_HISTORY = "data/review/adjudication-v1/decision-history.json"
PARENT_LATEST = "data/review/adjudication-v1/latest-decisions.json"
PRIVATE_NAMES = (
    "approval.json",
    "completed.csv",
    "decision-history.json",
    "latest-decisions.json",
)


def implementation_hashes(root: Path) -> dict:
    return {
        path.relative_to(root).as_posix(): sha256(path)
        for path in [*sorted((root / "pipeline/review_acceptance").glob("*.py")), root / "uv.lock"]
    }


def bound(root: Path, receipt: dict, name: str) -> bytes:
    content = (root / name).read_bytes()
    if receipt["artifacts"][name] != {"sha256": digest(content), "bytes": len(content)}:
        raise ValueError(f"Accepted artifact changed: {name}")
    return content


def validate_approval(approval: dict, sample: dict) -> None:
    expected = {case["pair_id"]: case["research"]["recommendation"] for case in sample["cases"]}
    if (
        approval.get("schema_version") != "explicit-chat-approval-v1"
        or approval.get("capture_method") != "explicit_user_chat_approval"
        or approval.get("batch_id") != "development60-v1"
        or approval.get("sample_sha256") != sample["sample_sha256"]
        or approval.get("research_sha256") != sample["research_sha256"]
        or approval.get("approved_labels") != expected
        or len(expected) != 60
        or not isinstance(approval.get("reviewer"), str)
        or not approval["reviewer"].strip()
        or not isinstance(approval.get("user_message"), str)
        or not approval["user_message"].strip()
        or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(approval.get("approved_on", "")))
        or approval.get("assistance") != "agent_research_and_second_review"
    ):
        raise ValueError(
            "Approval must explicitly bind all 60 final recommendations and provenance"
        )


def completed_csv(approval: dict, sample: dict) -> bytes:
    """Administrative transcription of chat approval; never claim this was a user export."""
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=HEADERS, lineterminator="\n")
    writer.writeheader()
    for case in sample["cases"]:
        writer.writerow(
            {
                **{key: case[key] for key in HEADERS[:3]},
                "label": approval["approved_labels"][case["pair_id"]],
                "reviewer": approval["reviewer"],
                "notes": (
                    "Administrative transcription: final researched recommendation approved "
                    f"by explicit chat message on {approval['approved_on']}. "
                    "See approval.json for the exact statement and evidence binding."
                ),
                "sample_sha256": sample["sample_sha256"],
            }
        )
    return stream.getvalue().encode()


def append_approval(
    history: dict, rows: list[dict], approval: dict, csv_hash: str, approval_hash: str
) -> tuple:
    if history.get("schema_version") != "decision-history-v1" or any(
        entry["round_id"] == ROUND for entry in history["rounds"]
    ):
        raise ValueError("Approval must append to the frozen pre-approval history")
    updated = copy.deepcopy(history)
    existing = {event["pair_id"] for event in history["events"]}
    seen = set()
    for row in rows:
        if (
            set(row) != set(HEADERS)
            or row["pair_id"] in existing | seen
            or row["label"] != approval["approved_labels"].get(row["pair_id"])
            or not row["label"]
        ):
            raise ValueError("Approval may only append unique new, explicitly approved decisions")
        seen.add(row["pair_id"])
        event = {
            **row,
            "round_id": ROUND,
            "source_csv_sha256": csv_hash,
            "approval_sha256": approval_hash,
            "capture_method": approval["capture_method"],
            "assistance": approval["assistance"],
            "approved_on": approval["approved_on"],
        }
        event["event_id"] = "decision_" + digest(json_bytes(event))
        updated["events"].append(event)
    if seen != set(approval["approved_labels"]):
        raise ValueError("Approval rows do not cover the explicit approval")
    updated["rounds"].append(
        {
            "round_id": ROUND,
            "source_csv_sha256": csv_hash,
            "approval_sha256": approval_hash,
            "capture_method": approval["capture_method"],
            "event_count": len(rows),
        }
    )
    by_pair = defaultdict(list)
    for event in updated["events"]:
        by_pair[event["pair_id"]].append(event)
    latest = []
    for _, events in sorted(by_pair.items()):
        newest = events[-1]
        latest.append(
            {
                **{key: newest[key] for key in HEADERS},
                "round_id": newest["round_id"],
                "latest_event_id": newest["event_id"],
                "history_event_ids": [event["event_id"] for event in events],
            }
        )
    return updated, {
        "schema_version": "latest-human-decisions-v1",
        "history_sha256": digest(json_bytes(updated)),
        "decisions": latest,
        "counts": dict(sorted(Counter(row["label"] for row in latest).items())),
        "policy": (
            "Latest explicit human decisions. Research-assisted approvals are identified in "
            "history; unresolved relationships remain unresolved. No automatic site merges."
        ),
    }


def render_report(receipt: dict) -> bytes:
    review = receipt["review"]
    rows = "\n".join(f"| {key} | {count} |" for key, count in review["approved_counts"].items())
    return f"""# First 60 researched recommendations accepted

Raj explicitly approved all final recommendations in chat on {receipt["approved_on"]}.
The private approval record preserves the exact message, reviewer, sample checksum,
research checksum and every approved pair/label. The completed CSV is an administrative
transcription of that approval, not a browser export or independently blind annotation.

| Accepted relationship | Cases |
| --- | ---: |
{rows}

The new round adds 60 decisions to the earlier 47 events. History now contains
107 events across 94 unique comparisons. All earlier events and source artifacts
are preserved. The latest view has {review["latest_unresolved"]} unresolved relationships:
seven earlier cases plus 26 approved as unresolved in this batch. Those answers do
not become binary match/nonmatch labels or automatic merges.

There are 107 new comparisons still unreviewed: 27 development and 80 holdout cases.
The frozen validation design contains 200 units, including 33 prior comparisons;
one additional historical publisher-view duplicate stays in history but outside it.
No model fitting, precision/recall claim, canonical identity creation or publication
is authorized by this approval. Formal validation remains incomplete.

Private accepted outputs are in `{DIRECTORY}/`. The append-only history and
latest-decision view there supersede earlier views for subsequent review work.
Earlier receipts remain immutable snapshots. CI verifies this aggregate receipt;
local checking rebuilds the private acceptance artifacts byte-for-byte.
""".encode()


def build(root: Path, approval_path: Path) -> tuple[dict, dict]:
    parent = check_research(root, local=True)
    preview = json.loads((root / "reports/private-preview-audit.json").read_text())
    history = json.loads(bound(root, preview, PARENT_HISTORY))
    previous = json.loads(bound(root, preview, PARENT_LATEST))
    sample = json.loads(bound(root, parent, f"{RESEARCH_DIRECTORY}/sample.json"))
    approval_bytes = approval_path.read_bytes()
    approval = json.loads(approval_bytes)
    validate_approval(approval, sample)
    csv_bytes = completed_csv(approval, sample)
    with tempfile.TemporaryDirectory(prefix="atlas-approval-check-") as temporary:
        path = Path(temporary) / "completed.csv"
        path.write_bytes(csv_bytes)
        validate_export(path, sample, complete=True)
    rows = list(csv.DictReader(io.StringIO(csv_bytes.decode())))
    updated, latest = append_approval(
        history, rows, approval, digest(csv_bytes), digest(approval_bytes)
    )
    if len(history["events"]) != 47 or len(previous["decisions"]) != 34:
        raise ValueError("Unexpected pre-approval history size")
    outputs = {
        f"{DIRECTORY}/approval.json": approval_bytes,
        f"{DIRECTORY}/completed.csv": csv_bytes,
        f"{DIRECTORY}/decision-history.json": json_bytes(updated),
        f"{DIRECTORY}/latest-decisions.json": json_bytes(latest),
    }
    receipt = {
        "schema_version": "development60-accepted-v1",
        "status": "explicit_human_approval_recorded",
        "approved_on": approval["approved_on"],
        "parent_receipt_sha256": sha256(root / RESEARCH_RECEIPT),
        "sample_sha256": sample["sample_sha256"],
        "review": {
            "previous_events_preserved": 47,
            "new_approved_decisions": 60,
            "history_events": len(updated["events"]),
            "unique_comparisons": len(latest["decisions"]),
            "approved_counts": dict(sorted(Counter(row["label"] for row in rows).items())),
            "latest_counts": latest["counts"],
            "latest_unresolved": sum(row["label"] in UNRESOLVED for row in latest["decisions"]),
            "unreviewed_development": 27,
            "unreviewed_holdout": 80,
        },
        "implementation": implementation_hashes(root),
        "model_fitted": False,
        "auto_merges": 0,
        "formal_gold_set_complete": False,
    }
    outputs[REPORT] = render_report(receipt)
    receipt["artifacts"] = {
        name: {"sha256": digest(value), "bytes": len(value)}
        for name, value in sorted(outputs.items())
    }
    if approval_path.read_bytes() != approval_bytes:
        raise ValueError("Approval input changed during acceptance")
    validate_receipt(receipt, parent)
    return outputs, receipt


def validate_receipt(receipt: dict, parent: dict) -> None:
    review = receipt["review"]
    if (
        receipt["schema_version"] != "development60-accepted-v1"
        or receipt["status"] != "explicit_human_approval_recorded"
        or receipt["model_fitted"] is not False
        or receipt["auto_merges"] != 0
        or receipt["formal_gold_set_complete"] is not False
        or review["previous_events_preserved"] != 47
        or review["new_approved_decisions"] != 60
        or review["history_events"] != 107
        or review["unique_comparisons"] != 94
        or sum(review["latest_counts"].values()) != 94
        or review["latest_unresolved"] != 33
        or review["approved_counts"] != parent["research"]["recommendation_counts"]
        or review["unreviewed_development"] != 27
        or review["unreviewed_holdout"] != 80
        or set(receipt["artifacts"]) != {REPORT, *(f"{DIRECTORY}/{n}" for n in PRIVATE_NAMES)}
    ):
        raise ValueError("Invalid approval aggregate receipt")


def prepare(root: Path, approval_path: Path | None = None) -> dict:
    outputs, receipt = build(root, approval_path or root / DIRECTORY / "approval.json")
    outputs[RECEIPT] = json_bytes(receipt)
    with tempfile.TemporaryDirectory(prefix=".approval-", dir=root / "data") as temporary:
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
    receipt = json.loads((root / RECEIPT).read_text())
    validate_receipt(receipt, parent)
    if (
        receipt["implementation"] != implementation_hashes(root)
        or receipt["parent_receipt_sha256"] != sha256(root / RESEARCH_RECEIPT)
        or receipt["sample_sha256"] != parent["sample_sha256"]
        or bound(root, receipt, REPORT) != render_report(receipt)
    ):
        raise ValueError("Approval code, report or parent receipt changed")
    if local:
        outputs, rebuilt = build(root, root / DIRECTORY / "approval.json")
        if rebuilt != receipt:
            raise ValueError("Rebuilt approval receipt changed")
        for name, content in outputs.items():
            if (root / name).read_bytes() != content:
                raise ValueError(f"Approval artifact changed: {name}")
    return receipt
