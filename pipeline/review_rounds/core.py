from __future__ import annotations

import copy
import csv
import io
import json
import tempfile
from collections import Counter, defaultdict
from pathlib import Path

from pipeline.link.labels import HEADERS
from pipeline.normalize.core import accept_outputs, sha256
from pipeline.reconciliation.core import validate_export
from pipeline.reconciliation.ledger import UNRESOLVED
from pipeline.review_acceptance.core import DIRECTORY as PREVIOUS_DIRECTORY
from pipeline.review_acceptance.core import RECEIPT as PREVIOUS_RECEIPT
from pipeline.review_acceptance.core import bound
from pipeline.review_next.core import DIRECTORY as SAMPLE_DIRECTORY
from pipeline.review_next.core import RECEIPT as SAMPLE_RECEIPT
from pipeline.review_next.core import check as check_parent
from pipeline.scaffold import digest, json_bytes

DIRECTORY = "data/review/next60-completed-v1"
RECEIPT = "reports/next60-completed-audit.json"
REPORT = "reports/next60-completed.md"
PARTS = {"development": 27, "holdout": 33}


def implementation_hashes(root: Path) -> dict:
    return {
        p.relative_to(root).as_posix(): sha256(p)
        for p in [*sorted((root / "pipeline/review_rounds").glob("*.py")), root / "uv.lock"]
    }


def append_round(history: dict, rows: list, part: str, source_hash: str) -> tuple:
    if part not in PARTS or len(rows) != PARTS[part]:
        raise ValueError("Wrong review part or row count")
    round_id = f"next60-completed-v1-{part}"
    if any(r["round_id"] == round_id for r in history["rounds"]):
        raise ValueError("Round already imported")
    updated = copy.deepcopy(history)
    prior = {e["pair_id"] for e in history["events"]}
    seen = set()
    assistance = (
        "agent_research_before_human_review" if part == "development" else "blind_source_only"
    )
    for row in rows:
        if (
            set(row) != set(HEADERS)
            or row["pair_id"] in prior | seen
            or not row["label"]
            or not row["reviewer"].strip()
            or row["left_record_id"] == row["right_record_id"]
        ):
            raise ValueError("Round must contain new, complete, distinct human decisions")
        seen.add(row["pair_id"])
        event = {
            **row,
            "round_id": round_id,
            "source_csv_sha256": source_hash,
            "capture_method": "user_submitted_browser_export",
            "assistance": assistance,
            "original_split": part,
        }
        event["event_id"] = "decision_" + digest(json_bytes(event))
        updated["events"].append(event)
    updated["rounds"].append(
        {
            "round_id": round_id,
            "source_csv_sha256": source_hash,
            "event_count": len(rows),
            "assistance": assistance,
        }
    )
    by_pair = defaultdict(list)
    for event in updated["events"]:
        by_pair[event["pair_id"]].append(event)
    latest_rows = []
    for _, events in sorted(by_pair.items()):
        newest = events[-1]
        latest_rows.append(
            {
                **{key: newest[key] for key in HEADERS},
                "round_id": newest["round_id"],
                "latest_event_id": newest["event_id"],
                "history_event_ids": [e["event_id"] for e in events],
            }
        )
    return updated, {
        "schema_version": "latest-human-decisions-v1",
        "history_sha256": digest(json_bytes(updated)),
        "decisions": latest_rows,
        "counts": dict(sorted(Counter(r["label"] for r in latest_rows).items())),
        "policy": "Human answers only; abstentions and containment are not identity merges.",
    }


def render_report(receipt: dict) -> bytes:
    r = receipt["review"]
    return f"""# Completed next-60 review imported

The original browser exports are preserved byte-for-byte: 27 researched development
answers and 33 blind source-only holdout answers. These add 60 human events without
altering the previous 107 events. The history now has {r["history_events"]} events
across {r["unique_comparisons"]} comparisons; {r["unresolved"]} remain unresolved.

The formal design has 153 of 200 units human-reviewed: all 120 development and 33
holdout. One historical publisher-view comparison remains outside that design.
The final 47 have no human answers yet. Their new agent-assisted status is governed
by a separate protocol change; no recommendation enters this human ledger.

The 33 holdout answers were collected blind. They are reserved for evaluation after
model and decision settings are frozen, never for fitting or error-driven tuning.
Unresolved relationships stay abstentions; directed containment stays separate from
same-site identity. No canonical merges or publication occur during intake.

Private files: `{DIRECTORY}/`. Local checking rebuilds these artifacts; offline CI
checks their aggregate receipt and implementation/parent hashes.
""".encode()


def build(root: Path, development: Path, holdout: Path) -> tuple:
    parent = check_parent(root, local=True)
    previous = json.loads((root / PREVIOUS_RECEIPT).read_text())
    history = json.loads(bound(root, previous, f"{PREVIOUS_DIRECTORY}/decision-history.json"))
    original_history = copy.deepcopy(history)
    outputs, counts, samples = {}, {}, {}
    for part, path in (("development", development), ("holdout", holdout)):
        sample = json.loads(bound(root, parent, f"{SAMPLE_DIRECTORY}/{part}.json"))
        raw = path.read_bytes()
        with tempfile.TemporaryDirectory() as temporary:
            check_path = Path(temporary) / "export.csv"
            check_path.write_bytes(raw)
            counts[part] = validate_export(check_path, sample, complete=True)["counts"]
        rows = list(csv.DictReader(io.StringIO(raw.decode("utf-8-sig"))))
        history, latest = append_round(history, rows, part, digest(raw))
        outputs[f"{DIRECTORY}/{part}.csv"] = raw
        samples[part] = sample["sample_sha256"]
        if path.read_bytes() != raw:
            raise ValueError("Input changed during acceptance")
    assert history["events"][:107] == original_history["events"]
    outputs[f"{DIRECTORY}/decision-history.json"] = json_bytes(history)
    outputs[f"{DIRECTORY}/latest-decisions.json"] = json_bytes(latest)
    receipt = {
        "schema_version": "next60-completed-v1",
        "parent_sha256": sha256(root / SAMPLE_RECEIPT),
        "previous_approval_sha256": sha256(root / PREVIOUS_RECEIPT),
        "sample_sha256": samples,
        "review": {
            "previous_events_preserved": len(original_history["events"]),
            "new_human_events": 60,
            "history_events": len(history["events"]),
            "unique_comparisons": len(latest["decisions"]),
            "unresolved": sum(r["label"] in UNRESOLVED for r in latest["decisions"]),
            "counts_by_round": counts,
            "latest_counts": latest["counts"],
            "formal_human_reviewed": 153,
            "pending_human_review": 47,
        },
        "implementation": implementation_hashes(root),
        "model_fitted": False,
        "auto_merges": 0,
    }
    outputs[REPORT] = render_report(receipt)
    receipt["artifacts"] = {
        n: {"sha256": digest(v), "bytes": len(v)} for n, v in sorted(outputs.items())
    }
    validate_receipt(receipt)
    return outputs, receipt


def validate_receipt(receipt: dict) -> None:
    review = receipt["review"]
    if (
        receipt["schema_version"] != "next60-completed-v1"
        or review["previous_events_preserved"] != 107
        or review["new_human_events"] != 60
        or review["history_events"] != 167
        or review["unique_comparisons"] != 154
        or sum(review["latest_counts"].values()) != 154
        or {p: sum(v.values()) for p, v in review["counts_by_round"].items()} != PARTS
        or review["formal_human_reviewed"] != 153
        or review["pending_human_review"] != 47
        or receipt["model_fitted"] is not False
        or receipt["auto_merges"] != 0
        or set(receipt["artifacts"])
        != {
            REPORT,
            *(
                f"{DIRECTORY}/{n}"
                for n in (
                    "development.csv",
                    "holdout.csv",
                    "decision-history.json",
                    "latest-decisions.json",
                )
            ),
        }
    ):
        raise ValueError("Invalid completed-round receipt")


def prepare(root: Path, development: Path | None = None, holdout: Path | None = None) -> dict:
    outputs, receipt = build(
        root,
        development or root / DIRECTORY / "development.csv",
        holdout or root / DIRECTORY / "holdout.csv",
    )
    outputs[RECEIPT] = json_bytes(receipt)
    with tempfile.TemporaryDirectory(prefix=".completed60-", dir=root / "data") as temporary:
        staged = []
        for name, content in outputs.items():
            path = Path(temporary) / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
            staged.append((path, root / name))
        accept_outputs(staged)
    return receipt


def check(root: Path, *, local=False) -> dict:
    parent = check_parent(root)
    receipt = json.loads((root / RECEIPT).read_text())
    validate_receipt(receipt)
    if (
        receipt["implementation"] != implementation_hashes(root)
        or receipt["parent_sha256"] != sha256(root / SAMPLE_RECEIPT)
        or receipt["previous_approval_sha256"] != sha256(root / PREVIOUS_RECEIPT)
        or receipt["sample_sha256"] != {p: parent["packets"][p]["sample_sha256"] for p in PARTS}
        or bound(root, receipt, REPORT) != render_report(receipt)
    ):
        raise ValueError("Completed-review parent, code or report changed")
    if local:
        outputs, rebuilt = build(
            root, root / DIRECTORY / "development.csv", root / DIRECTORY / "holdout.csv"
        )
        if rebuilt != receipt or any((root / n).read_bytes() != v for n, v in outputs.items()):
            raise ValueError("Completed-review rebuild changed")
    return receipt
