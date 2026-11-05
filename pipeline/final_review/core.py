from __future__ import annotations

import copy
import json
import re
import tempfile
from pathlib import Path

from pipeline.final_review.pages import dossier, workspace
from pipeline.final_review.validation import validate_research
from pipeline.normalize.core import accept_outputs, sha256
from pipeline.reconciliation.core import load_packet, validate_export
from pipeline.research_review.core import research_summary
from pipeline.research_review.review_page import LABEL_NAMES
from pipeline.review_acceptance.core import bound
from pipeline.review_rounds.core import RECEIPT as COMPLETED_RECEIPT
from pipeline.review_rounds.core import check as check_completed
from pipeline.scaffold import digest, json_bytes
from pipeline.triage.labels import blank_csv, sample_digest

DIRECTORY = "data/review/final47-v1"
RECEIPT = "reports/final47-audit.json"
REPORT = "reports/final47-review.md"
PROTOCOL = "experiments/matching/protocol.json"
PRIVATE_NAMES = (
    "research-input.json",
    "protocol.json",
    "sample.json",
    "decisions-blank.csv",
    "index.html",
    "analysis.html",
)


def implementation_hashes(root: Path) -> dict:
    return {
        p.relative_to(root).as_posix(): sha256(p)
        for p in [
            *sorted((root / "pipeline/final_review").glob("*.py")),
            root / "uv.lock",
            root / PROTOCOL,
        ]
    }


def render_report(receipt: dict) -> bytes:
    research = receipt["research"]
    rows = "\n".join(
        f"| {label} | {count} |" for label, count in research["recommendation_counts"].items()
    )
    return f"""# Final 47: researched recommendations for approval

Raj changed the plan on 2026-11-04: research and recommend the final 47 instead of
keeping them blind, then test matching robustly. The exact instruction and revised
evaluation protocol are retained. This packet uses the original holdout positions
34–80 without reselection; its status is now **agent-assisted audit, not blind**.

The completed 27 development and 33 blind exports are accepted separately. All 153
human-reviewed formal units remain human decisions. These 47 proposals are not
human labels, do not complete the original 200-human-review requirement, and are
excluded from fitting the experimental matcher. That experiment evaluates the 33
blind human answers after freezing; agent-agreement is reported separately.

| Proposed relationship | Cases |
| --- | ---: |
{rows}

All 47 cases have primary-source investigation and a separate agent's second review.
The dossier contains {research["citation_count"]} citation entries across
{research["unique_citation_urls"]} URLs, with access limits and shared provenance
disclosed. {research["recommendations_changed_after_review"]} relationship proposals
changed during review. Unresolved proposals remain explicit evidence limits.

Open `{DIRECTORY}/index.html` to approve or revise; `analysis.html` contains the full
printable dossier. Answers start blank and save in a new browser namespace. Export
a CSV with your reviewer name. No prior decision is overwritten and no proposal is
automatically accepted. The CSV's packet hash prevents import into another batch.

No canonical sites are merged and no observation data is published. Each local
rebuild must reproduce accepted bytes; CI verifies aggregate receipts only.
""".encode()


def build(root: Path, research_path: Path) -> tuple:
    completed = check_completed(root, local=True)
    parent = json.loads((root / "reports/reconciliation-audit.json").read_text())
    holdout = load_packet(root, parent, "holdout")
    selected = copy.deepcopy(holdout["cases"][33:])
    raw = research_path.read_bytes()
    research = json.loads(raw)
    proposals = validate_research(research, selected)
    for case, proposal in zip(selected, proposals, strict=True):
        case["research"] = proposal
    protocol = (root / PROTOCOL).read_bytes()
    sample = {
        "schema_version": "validation-review-v1",
        "mode": "adjudication",
        "original_split": "holdout",
        "evaluation_role": "agent_assisted_audit_not_blind",
        "parent_sample_sha256": holdout["sample_sha256"],
        "research_sha256": digest(raw),
        "protocol_sha256": digest(protocol),
        "cases": selected,
    }
    sample["sample_sha256"] = sample_digest(sample)
    summary = research_summary(proposals)
    names = json.loads((root / "data/review/linkage/sample.json").read_text())["source_names"]
    outputs = {
        f"{DIRECTORY}/research-input.json": raw,
        f"{DIRECTORY}/protocol.json": protocol,
        f"{DIRECTORY}/sample.json": json_bytes(sample),
        f"{DIRECTORY}/decisions-blank.csv": blank_csv(selected, sample["sample_sha256"]),
        f"{DIRECTORY}/index.html": workspace(selected, sample["sample_sha256"], names),
        f"{DIRECTORY}/analysis.html": dossier(selected, summary),
    }
    receipt = {
        "schema_version": "final47-assisted-v1",
        "status": "pending_human_approval",
        "completed_receipt_sha256": sha256(root / COMPLETED_RECEIPT),
        "parent_receipt_sha256": sha256(root / "reports/reconciliation-audit.json"),
        "original_holdout_sample_sha256": holdout["sample_sha256"],
        "sample_sha256": sample["sample_sha256"],
        "protocol_sha256": digest(protocol),
        "selection": "Original holdout[33:80]; order unchanged",
        "research": summary,
        "human_reviewed_formal_units": completed["review"]["formal_human_reviewed"],
        "new_human_labels": 0,
        "blind_evaluation_cases": 0,
        "auto_merges": 0,
        "implementation": implementation_hashes(root),
    }
    outputs[REPORT] = render_report(receipt)
    receipt["artifacts"] = {
        n: {"sha256": digest(v), "bytes": len(v)} for n, v in sorted(outputs.items())
    }
    if research_path.read_bytes() != raw:
        raise ValueError("Research changed during preparation")
    validate_receipt(receipt)
    return outputs, receipt


def validate_receipt(receipt: dict) -> None:
    r = receipt.get("research")
    if not isinstance(r, dict):
        raise ValueError("Invalid final47 research summary")
    count_groups = {
        "recommendation_counts": set(LABEL_NAMES),
        "confidence_counts": {"high", "medium", "low"},
        "second_review_outcomes": {"agree", "revise", "caution"},
        "citation_access_counts": {"full_text", "indexed_extract", "local_snapshot"},
    }
    for key, allowed in count_groups.items():
        counts = r.get(key)
        if (
            not isinstance(counts, dict)
            or set(counts) - allowed
            or any(type(count) is not int or count < 0 for count in counts.values())
        ):
            raise ValueError("Invalid final47 research counts")
    for values, keys in (
        (
            r,
            (
                "case_count",
                "independently_reviewed",
                "citation_count",
                "unique_citation_urls",
                "recommendations_changed_after_review",
            ),
        ),
        (
            receipt,
            (
                "human_reviewed_formal_units",
                "new_human_labels",
                "blind_evaluation_cases",
                "auto_merges",
            ),
        ),
    ):
        if any(type(values.get(key)) is not int or values[key] < 0 for key in keys):
            raise ValueError("Final47 counts require nonnegative integers")
    if (
        receipt.get("schema_version") != "final47-assisted-v1"
        or receipt.get("status") != "pending_human_approval"
        or receipt.get("selection") != "Original holdout[33:80]; order unchanged"
        or r["case_count"] != 47
        or r["independently_reviewed"] != 47
        or sum(r["recommendation_counts"].values()) != 47
        or sum(r["confidence_counts"].values()) != 47
        or sum(r["second_review_outcomes"].values()) != 47
        or r["citation_count"] != sum(r["citation_access_counts"].values())
        or r["citation_count"] < 47
        or not 1 <= r["unique_citation_urls"] <= r["citation_count"]
        or r["recommendations_changed_after_review"] > 47
        or sum(r["citation_access_counts"].get(k, 0) for k in ("full_text", "indexed_extract")) < 47
        or receipt["human_reviewed_formal_units"] != 153
        or receipt["new_human_labels"] != 0
        or receipt["blind_evaluation_cases"] != 0
        or receipt["auto_merges"] != 0
        or not isinstance(receipt.get("artifacts"), dict)
        or set(receipt["artifacts"]) != {REPORT, *(f"{DIRECTORY}/{n}" for n in PRIVATE_NAMES)}
    ):
        raise ValueError("Invalid final47 receipt")
    hashes = [
        receipt.get(key)
        for key in (
            "completed_receipt_sha256",
            "parent_receipt_sha256",
            "original_holdout_sample_sha256",
            "sample_sha256",
            "protocol_sha256",
        )
    ]
    implementation = receipt.get("implementation")
    if not isinstance(implementation, dict) or not implementation:
        raise ValueError("Invalid final47 implementation checksums")
    hashes.extend(implementation.values())
    for entry in receipt["artifacts"].values():
        if (
            not isinstance(entry, dict)
            or set(entry) != {"sha256", "bytes"}
            or type(entry["bytes"]) is not int
            or entry["bytes"] < 1
        ):
            raise ValueError("Invalid final47 artifact checksum or size")
        hashes.append(entry["sha256"])
    if any(
        not isinstance(value, str) or not re.fullmatch(r"[a-f0-9]{64}", value) for value in hashes
    ):
        raise ValueError("Invalid final47 checksum")


def prepare(root: Path, research_path: Path | None = None) -> dict:
    outputs, receipt = build(root, research_path or root / DIRECTORY / "research-input.json")
    outputs[RECEIPT] = json_bytes(receipt)
    with tempfile.TemporaryDirectory(prefix=".final47-", dir=root / "data") as temporary:
        staged = []
        for name, data in outputs.items():
            p = Path(temporary) / name
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(data)
            staged.append((p, root / name))
        accept_outputs(staged)
    return receipt


def check(root: Path, *, local=False) -> dict:
    check_completed(root)
    receipt = json.loads((root / RECEIPT).read_text())
    validate_receipt(receipt)
    parent = json.loads((root / "reports/reconciliation-audit.json").read_text())
    if (
        receipt["implementation"] != implementation_hashes(root)
        or receipt["completed_receipt_sha256"] != sha256(root / COMPLETED_RECEIPT)
        or receipt["parent_receipt_sha256"] != sha256(root / "reports/reconciliation-audit.json")
        or receipt["original_holdout_sample_sha256"]
        != parent["packets"]["holdout"]["sample_sha256"]
        or receipt["protocol_sha256"] != sha256(root / PROTOCOL)
        or bound(root, receipt, REPORT) != render_report(receipt)
    ):
        raise ValueError("Final47 code, protocol, parent or report changed")
    if local:
        outputs, rebuilt = build(root, root / DIRECTORY / "research-input.json")
        if rebuilt != receipt or any((root / n).read_bytes() != v for n, v in outputs.items()):
            raise ValueError("Final47 rebuild changed")
    return receipt


def verify_labels(root: Path, path: Path, *, complete=False) -> dict:
    receipt = check(root)
    sample = json.loads(bound(root, receipt, f"{DIRECTORY}/sample.json"))
    return validate_export(path, sample, complete=complete)
