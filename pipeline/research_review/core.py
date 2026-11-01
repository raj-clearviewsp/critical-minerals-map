"""Freeze a researched subset of development cases without creating human labels."""

from __future__ import annotations

import copy
import json
import re
import tempfile
from collections import Counter
from pathlib import Path

from pipeline.normalize.core import accept_outputs, sha256
from pipeline.preview.core import check as check_preview
from pipeline.preview.core import safe_url
from pipeline.reconciliation.core import load_packet, validate_export
from pipeline.research_review.review_page import LABEL_NAMES, render_dossier, render_workspace
from pipeline.scaffold import digest, json_bytes
from pipeline.triage.labels import blank_csv, sample_digest

DIRECTORY = "data/review/development60-v1"
RECEIPT = "reports/development60-audit.json"
REPORT = "reports/development60-review.md"
SIZE = 60
TEXT_FIELDS = (
    "summary",
    "left_interpretation",
    "right_interpretation",
    "geographic_analysis",
    "scope_analysis",
    "commodity_geology_analysis",
)
LIST_FIELDS = ("rationale", "counterarguments", "uncertainties", "what_would_change")
PRIVATE_NAMES = (
    "research-input.json",
    "sample.json",
    "decisions-blank.csv",
    "index.html",
    "analysis.html",
)


def implementation_hashes(root: Path) -> dict:
    return {
        p.relative_to(root).as_posix(): sha256(p)
        for p in [*sorted((root / "pipeline/research_review").glob("*.py")), root / "uv.lock"]
    }


def validate_research(research: dict, selected: list[dict]) -> list[dict]:
    if research.get("schema_version") != "researched-development60-v1":
        raise ValueError("Unknown research schema")
    cases = research.get("cases")
    if not isinstance(cases, list) or len(cases) != SIZE or len(selected) != SIZE:
        raise ValueError("Research must cover exactly 60 selected development cases")
    if [c.get("pair_id") for c in cases] != [c["pair_id"] for c in selected]:
        raise ValueError("Research order or selected pair identities changed")
    for number, case in enumerate(cases, 1):
        if case.get("case_number") != number or case.get("recommendation") not in LABEL_NAMES:
            raise ValueError("Invalid case number or recommendation")
        if case.get("confidence") not in {"high", "medium", "low"}:
            raise ValueError("Confidence must be an explicit qualitative judgment")
        if any(not isinstance(case.get(key), str) or not case[key].strip() for key in TEXT_FIELDS):
            raise ValueError(
                "Each case requires interpretations and geographic, scope and geology analysis"
            )
        for key in LIST_FIELDS:
            value = case.get(key)
            if not isinstance(value, list) or not all(
                isinstance(v, str) and v.strip() for v in value
            ):
                raise ValueError("Research arguments and uncertainty must be text lists")
        if (
            len(case["rationale"]) < 2
            or not case["counterarguments"]
            or not case["what_would_change"]
        ):
            raise ValueError(
                "Research must explain its reasoning, alternatives and revision conditions"
            )
        citations = case.get("citations")
        if not isinstance(citations, list) or not citations:
            raise ValueError("Every case needs cited evidence")
        for citation in citations:
            if not isinstance(citation, dict) or not safe_url(citation.get("url")):
                raise ValueError("Research citation must be a safe HTTPS URL")
            if any(
                not isinstance(citation.get(key), str) or not citation[key].strip()
                for key in ("title", "publisher", "locator", "claim", "independence_note")
            ):
                raise ValueError("Citation needs a claim, locator and independence assessment")
            if citation.get("access") not in {"full_text", "indexed_extract", "local_snapshot"}:
                raise ValueError("Citation access must be disclosed")
            if citation.get("source_date") is not None and not isinstance(
                citation["source_date"], str
            ):
                raise ValueError("Citation date must be text or explicitly unknown")
        if all(c["access"] == "local_snapshot" for c in citations):
            raise ValueError("Every case needs an external source check")
        second = case.get("second_review")
        if not isinstance(second, dict) or (
            second.get("reviewer") == case.get("analyst")
            or not isinstance(second.get("reviewer"), str)
            or not second["reviewer"].strip()
            or second.get("outcome") not in {"agree", "revise", "caution"}
            or not isinstance(second.get("note"), str)
            or not second["note"].strip()
            or not isinstance(second.get("verifications"), list)
            or not all(isinstance(v, str) and v.strip() for v in second["verifications"])
        ):
            raise ValueError("Every case needs a separate reviewer and an explicit review result")
        if not isinstance(case.get("analyst"), str) or not case["analyst"].strip():
            raise ValueError("Research analyst must be identified")
        if case.get("initial_recommendation") not in LABEL_NAMES:
            raise ValueError("Preserve the analyst's initial recommendation")
        if case["initial_recommendation"] != case["recommendation"] and not case.get(
            "editorial_review"
        ):
            raise ValueError("Recommendation changes require an editorial explanation")
    return copy.deepcopy(cases)


def research_summary(cases: list[dict]) -> dict:
    return {
        "case_count": len(cases),
        "recommendation_counts": dict(sorted(Counter(c["recommendation"] for c in cases).items())),
        "confidence_counts": dict(sorted(Counter(c["confidence"] for c in cases).items())),
        "second_review_outcomes": dict(
            sorted(Counter(c["second_review"]["outcome"] for c in cases).items())
        ),
        "citation_count": sum(len(c["citations"]) for c in cases),
        "unique_citation_urls": len({c["url"] for case in cases for c in case["citations"]}),
        "citation_access_counts": dict(
            sorted(Counter(c["access"] for case in cases for c in case["citations"]).items())
        ),
        "independently_reviewed": len(cases),
        "recommendations_changed_after_review": sum(
            c["initial_recommendation"] != c["recommendation"] for c in cases
        ),
    }


def render_report(receipt: dict) -> bytes:
    summary = receipt["research"]
    rows = "\n".join(
        f"| {LABEL_NAMES[key]} | {count} |"
        for key, count in summary["recommendation_counts"].items()
    )
    text = f"""# Sixty researched development comparisons

**Status: researched recommendations ready for human approval. No new human labels created.**

This batch takes the first 60 of the 87 unreviewed development comparisons in their
frozen order. Three research agents investigated separate sets of 20; a different
agent challenged each case in a second pass. The final packet records the reasons,
competing interpretations, remaining uncertainty, conditions for changing the answer,
and explicit source-access and source-independence limitations.

| Proposed relationship | Cases |
| --- | ---: |
{rows}

There are {summary["citation_count"]} citation entries across {summary["unique_citation_urls"]}
distinct URLs. All 60 cases received an independent second review;
{summary["recommendations_changed_after_review"]} recommendations changed after review.
Confidence labels are qualitative judgments, not calibrated probabilities. A
high-confidence recommendation to leave scope unresolved is not a resolved identity.

## Review files

Open `data/review/development60-v1/index.html` for the approval workspace, or
`analysis.html` in the same directory for the complete searchable and printable
60-case dossier. All detailed source data and research stay local and Git-ignored.
Each case shows a recommendation and starts with a blank human decision. “Use this
recommendation” fills only the current case. Alternative relationships and notes
remain available. Enter your reviewer name and export partial or complete CSV
progress; browser storage alone is not a backup.

Validate an export with:

```sh
uv run --locked python -m pipeline.research_review --labels /absolute/path/to/export.csv --complete
```

Omit `--complete` for partial progress. This verifies the exact batch and source
identities without importing or overwriting decisions. The submitted decisions
will later be appended as a separate human review round.

## Preserved boundaries

The earlier 47 review events, their 34 unique comparisons, and all accepted
source/preview artifacts remain unchanged. The 80-case holdout has not been
researched or given recommendations. The other 27 new development cases are
unchanged. Answering this batch will leave 107 new cases (27 development and 80
holdout), plus seven unresolved earlier relationships and any cases in this batch
approved as unresolved. Preparing suggestions does not create human decisions or
convert unresolved relationships into binary match/nonmatch labels.

These are research-assisted development labels after approval, not independent
blind annotations. Any evaluation must disclose that assistance. Holdout labels
remain separate and blind; no model has been fitted or threshold tuned. Same-site
identity, directed containment and unresolved association remain different outcomes.
No automatic merges, new canonical identities, data publication or source licenses
are inferred from these recommendations.

## Verification

The packet is bound to its frozen parent development sample, exact first-60 pair
order, unchanged source records, latest review receipt and research-input checksum.
The public receipt contains aggregate counts and checksums only. Local verification
rebuilds the full private package byte-for-byte; CI verifies code, receipt and report
without private files. Structured tests cover changed/reordered pairs, missing
research, second-review requirements, output conflicts, blank initial decisions,
CSV identity binding and HTML/script escaping.
"""
    return text.encode()


def build(root: Path, research_path: Path) -> tuple[dict[str, bytes], dict]:
    preview = check_preview(root)
    parent_receipt = json.loads((root / "reports/reconciliation-audit.json").read_text())
    parent = load_packet(root, parent_receipt, "development")
    selected = copy.deepcopy(parent["cases"][:SIZE])
    latest_path = "data/review/adjudication-v1/latest-decisions.json"
    latest_bytes = (root / latest_path).read_bytes()
    if digest(latest_bytes) != preview["artifacts"][latest_path]["sha256"]:
        raise ValueError("Latest preserved human decisions changed")
    previous = {c["pair_id"] for c in json.loads(latest_bytes)["decisions"]}
    if previous.intersection(c["pair_id"] for c in selected):
        raise ValueError("New batch contains an already reviewed comparison")
    raw_research = research_path.read_bytes()
    research = json.loads(raw_research)
    proposals = validate_research(research, selected)
    for case, proposal in zip(selected, proposals, strict=True):
        case["research"] = proposal
    sample = {
        "schema_version": "validation-review-v1",
        "mode": "development",
        "batch_id": "development60-v1",
        "parent_sample_sha256": parent["sample_sha256"],
        "research_sha256": digest(raw_research),
        "cases": selected,
    }
    sample["sample_sha256"] = sample_digest(sample)
    source_names = json.loads((root / "data/review/linkage/sample.json").read_text())[
        "source_names"
    ]
    summary = research_summary(proposals)
    outputs = {
        f"{DIRECTORY}/research-input.json": raw_research,
        f"{DIRECTORY}/sample.json": json_bytes(sample),
        f"{DIRECTORY}/decisions-blank.csv": blank_csv(selected, sample["sample_sha256"]),
        f"{DIRECTORY}/index.html": render_workspace(
            selected, sample["sample_sha256"], source_names
        ),
        f"{DIRECTORY}/analysis.html": render_dossier(selected, summary),
    }
    receipt = {
        "schema_version": "development60-audit-v1",
        "status": "researched_recommendations_pending_human_approval",
        "parent_receipt_sha256": sha256(root / "reports/reconciliation-audit.json"),
        "preview_receipt_sha256": sha256(root / "reports/private-preview-audit.json"),
        "parent_sample_sha256": parent["sample_sha256"],
        "sample_sha256": sample["sample_sha256"],
        "selection_policy": "First 60 of 87 frozen new development cases; parent order retained.",
        "research": summary,
        "review": {
            "previous_events_preserved": 47,
            "previous_unique_pairs": 34,
            "new_human_labels_created": 0,
            "development_remaining_after_60": 27,
            "holdout_cases_untouched": 80,
            "prior_unresolved": 7,
        },
        "implementation": implementation_hashes(root),
        "model_fitted": False,
        "auto_merges": 0,
        "canonical_rows_added": 0,
        "published_rows_added": 0,
        "privacy": (
            "Only aggregate counts and checksums are public; observations and research stay local."
        ),
    }
    outputs[REPORT] = render_report(receipt)
    receipt["artifacts"] = {
        name: {"sha256": digest(content), "bytes": len(content)}
        for name, content in sorted(outputs.items())
    }
    if research_path.read_bytes() != raw_research:
        raise ValueError("Research input changed during build")
    validate_receipt(receipt)
    return outputs, receipt


def validate_receipt(receipt: dict) -> None:
    research, review = receipt["research"], receipt["review"]
    if (
        receipt["schema_version"] != "development60-audit-v1"
        or receipt["status"] != "researched_recommendations_pending_human_approval"
        or receipt["model_fitted"] is not False
        or any(
            receipt[key] != 0
            for key in ("auto_merges", "canonical_rows_added", "published_rows_added")
        )
        or research["case_count"] != SIZE
        or research["independently_reviewed"] != SIZE
        or sum(research["recommendation_counts"].values()) != SIZE
        or set(research["recommendation_counts"]) - set(LABEL_NAMES)
        or sum(research["confidence_counts"].values()) != SIZE
        or set(research["confidence_counts"]) - {"high", "medium", "low"}
        or sum(research["second_review_outcomes"].values()) != SIZE
        or research["citation_count"] != sum(research["citation_access_counts"].values())
        or review
        != {
            "previous_events_preserved": 47,
            "previous_unique_pairs": 34,
            "new_human_labels_created": 0,
            "development_remaining_after_60": 27,
            "holdout_cases_untouched": 80,
            "prior_unresolved": 7,
        }
        or set(receipt["artifacts"]) != {REPORT, *(f"{DIRECTORY}/{name}" for name in PRIVATE_NAMES)}
    ):
        raise ValueError("Invalid researched development aggregate receipt")
    for entry in receipt["artifacts"].values():
        if (
            set(entry) != {"sha256", "bytes"}
            or not re.fullmatch(r"[a-f0-9]{64}", entry["sha256"])
            or type(entry["bytes"]) is not int
            or entry["bytes"] < 1
        ):
            raise ValueError("Invalid researched artifact checksum")


def prepare(root: Path, research_path: Path | None = None) -> dict:
    outputs, receipt = build(root, research_path or root / DIRECTORY / "research-input.json")
    outputs[RECEIPT] = json_bytes(receipt)
    with tempfile.TemporaryDirectory(prefix=".research60-", dir=root / "data") as temporary:
        staged = []
        for name, content in outputs.items():
            path = Path(temporary) / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
            staged.append((path, root / name))
        accept_outputs(staged)
    return receipt


def check(root: Path, *, local=False) -> dict:
    preview = check_preview(root)
    parent = json.loads((root / "reports/reconciliation-audit.json").read_text())
    receipt = json.loads((root / RECEIPT).read_text())
    validate_receipt(receipt)
    if (
        receipt["implementation"] != implementation_hashes(root)
        or receipt["parent_receipt_sha256"] != sha256(root / "reports/reconciliation-audit.json")
        or receipt["preview_receipt_sha256"] != sha256(root / "reports/private-preview-audit.json")
        or receipt["parent_sample_sha256"] != parent["packets"]["development"]["sample_sha256"]
        or receipt["review"]["previous_events_preserved"] != preview["review"]["history_events"]
        or receipt["review"]["previous_unique_pairs"] != preview["review"]["unique_comparisons"]
        or receipt["review"]["prior_unresolved"] != preview["review"]["unresolved_comparisons"]
        or parent["packets"]["development"]["cases"] - SIZE
        != receipt["review"]["development_remaining_after_60"]
        or parent["packets"]["holdout"]["cases"] != receipt["review"]["holdout_cases_untouched"]
        or (root / REPORT).read_bytes() != render_report(receipt)
        or receipt["artifacts"][REPORT]
        != {"sha256": digest(render_report(receipt)), "bytes": len(render_report(receipt))}
    ):
        raise ValueError("Research code, report or frozen parent changed")
    if local:
        outputs, rebuilt = build(root, root / DIRECTORY / "research-input.json")
        if rebuilt != receipt:
            raise ValueError("Rebuilt research receipt changed")
        for name, content in outputs.items():
            if (root / name).read_bytes() != content:
                raise ValueError(f"Research artifact changed: {name}")
    return receipt


def verify_labels(root: Path, path: Path, *, complete=False) -> dict:
    receipt = check(root)
    name = f"{DIRECTORY}/sample.json"
    content = (root / name).read_bytes()
    if receipt["artifacts"][name] != {"sha256": digest(content), "bytes": len(content)}:
        raise ValueError("Accepted researched sample changed")
    sample = json.loads(content)
    if (
        sample["sample_sha256"] != receipt["sample_sha256"]
        or sample["batch_id"] != "development60-v1"
    ):
        raise ValueError("Wrong researched review batch")
    return validate_export(path, sample, complete=complete)
