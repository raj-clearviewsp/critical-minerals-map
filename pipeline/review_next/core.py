"""Freeze 27 researched development and 33 untouched holdout comparisons separately."""

from __future__ import annotations

import copy
import json
import tempfile
from pathlib import Path

from pipeline.normalize.core import accept_outputs, sha256
from pipeline.preview.core import safe_url
from pipeline.reconciliation.core import load_packet, validate_export
from pipeline.reconciliation.review_page import BLINDED_KEYS, sanitize_cases
from pipeline.research_review.core import LIST_FIELDS, TEXT_FIELDS, research_summary
from pipeline.research_review.review_page import LABEL_NAMES
from pipeline.review_acceptance.core import DIRECTORY as ACCEPTED_DIRECTORY
from pipeline.review_acceptance.core import RECEIPT as ACCEPTED_RECEIPT
from pipeline.review_acceptance.core import bound
from pipeline.review_acceptance.core import check as check_acceptance
from pipeline.review_next.pages import analysis_page, development_page, holdout_page, index_page
from pipeline.scaffold import digest, json_bytes
from pipeline.triage.labels import blank_csv, sample_digest

DIRECTORY = "data/review/next60-v1"
RECEIPT = "reports/next60-audit.json"
REPORT = "reports/next60-review.md"
PARTS = {"development": 27, "holdout": 33}
PRIVATE_NAMES = (
    "research-input.json",
    "index.html",
    "analysis.html",
    *(f"{part}{suffix}" for part in PARTS for suffix in (".json", "-blank.csv", ".html")),
)


def implementation_hashes(root: Path) -> dict:
    return {
        path.relative_to(root).as_posix(): sha256(path)
        for path in [*sorted((root / "pipeline/review_next").glob("*.py")), root / "uv.lock"]
    }


def validate_research(research: dict, selected: list) -> list:
    """Validate this batch without altering the historical fixed-size validator."""
    cases = research.get("cases", [])
    if (
        research.get("schema_version") != "researched-development27-v1"
        or len(cases) != 27
        or len(selected) != 27
        or [c.get("pair_id") for c in cases] != [c["pair_id"] for c in selected]
    ):
        raise ValueError("Research must cover exactly the remaining 27 development cases in order")
    for number, case in enumerate(cases, 1):
        if (
            case.get("case_number") != number
            or case.get("recommendation") not in LABEL_NAMES
            or case.get("initial_recommendation") not in LABEL_NAMES
            or case.get("confidence") not in {"high", "medium", "low"}
            or any(not isinstance(case.get(k), str) or not case[k].strip() for k in TEXT_FIELDS)
            or not isinstance(case.get("analyst"), str)
            or not case["analyst"].strip()
        ):
            raise ValueError("Each case needs complete, attributed research and a valid proposal")
        for key in LIST_FIELDS:
            if not isinstance(case.get(key), list) or not all(
                isinstance(value, str) and value.strip() for value in case[key]
            ):
                raise ValueError("Arguments and uncertainty must be text lists")
        if (
            len(case["rationale"]) < 2
            or not case["counterarguments"]
            or not case["what_would_change"]
        ):
            raise ValueError("Explain evidence, alternatives and revision conditions")
        citations = case.get("citations")
        if not isinstance(citations, list) or not citations:
            raise ValueError("Every researched case requires citations")
        for citation in citations:
            if (
                not isinstance(citation, dict)
                or not safe_url(citation.get("url"))
                or any(
                    not isinstance(citation.get(k), str) or not citation[k].strip()
                    for k in ("title", "publisher", "locator", "claim", "independence_note")
                )
                or citation.get("access") not in {"full_text", "indexed_extract", "local_snapshot"}
                or (
                    citation.get("source_date") is not None
                    and not isinstance(citation["source_date"], str)
                )
            ):
                raise ValueError("Citations require safe URLs, locators and honest access details")
        if all(c["access"] == "local_snapshot" for c in citations):
            raise ValueError("Every case requires an external primary source check")
        peer = case.get("second_review", {})
        if (
            not isinstance(peer, dict)
            or not isinstance(peer.get("reviewer"), str)
            or not peer["reviewer"].strip()
            or peer["reviewer"] == case["analyst"]
            or peer.get("outcome") not in {"agree", "revise", "caution"}
            or not isinstance(peer.get("note"), str)
            or not peer["note"].strip()
            or not isinstance(peer.get("verifications"), list)
            or not peer["verifications"]
            or not all(isinstance(v, str) and v.strip() for v in peer["verifications"])
        ):
            raise ValueError("Every proposal requires a separate reviewer with substantive checks")
        if case["recommendation"] != case["initial_recommendation"] and not case.get(
            "editorial_review"
        ):
            raise ValueError("Changed recommendations require editorial explanation")
    return copy.deepcopy(cases)


def ensure_blind(value) -> None:
    if isinstance(value, dict):
        if set(value) & BLINDED_KEYS:
            raise ValueError("Blind payload contains research, labels or automated assessments")
        for item in value.values():
            ensure_blind(item)
    elif isinstance(value, list):
        for item in value:
            ensure_blind(item)


def render_report(receipt: dict) -> bytes:
    research = receipt["research"]
    rows = "\n".join(
        f"| {LABEL_NAMES[label]} | {count} |"
        for label, count in research["recommendation_counts"].items()
    )
    return f"""# Next 60 comparisons: 27 researched and 33 blind

Raj approved all 60 previous final recommendations on 2026-11-04. Their accepted
round is bound by `reports/development60-accepted-audit.json`. The append-only history
now has 107 events and 94 unique comparisons; 33 relationships remain unresolved.

Raj selected **27 researched + 33 blind** to preserve the existing validation plan.
This batch takes development positions 61–87 and holdout positions 1–33 in their
original frozen orders. No cases were chosen by research outcome. The other 47
holdout cases remain outside this batch. No model has been fitted or thresholds tuned.

| Proposed development relationship | Cases |
| --- | ---: |
{rows}

All 27 development cases received primary-source investigation and a separate
agent's second review. There are {research["citation_count"]} citation entries across
{research["unique_citation_urls"]} distinct URLs. The 33 holdout cases have no research,
recommendations, prior answers or automated scores in either JSON or HTML payloads.
They are packaged separately so development research cannot enter the blind view.

Open `{DIRECTORY}/index.html` for both sections. `development.html` contains the
27 researched cases and `analysis.html` their full dossier. `holdout.html` contains
33 source-evidence-only comparisons. Each section begins blank, saves progress in
its own browser namespace and exports a separate sample-bound CSV.

```sh
uv run --locked python -m pipeline.review_next --labels /absolute/path/to/development.csv \\
    --packet development --complete
uv run --locked python -m pipeline.review_next --labels /absolute/path/to/holdout.csv \\
    --packet holdout --complete
```

Omit `--complete` for partial progress. Verification does not import or overwrite
answers. Research-assisted development approvals remain distinct from blind human
holdout annotations. Completing this batch leaves 47 new comparisons plus earlier
and newly unresolved relationships; unresolved answers are not binary gold labels.
There are no new human decisions, merges, canonical IDs or public data in this batch.

All earlier source, review and preview artifacts remain immutable. Local checks
rebuild this packet byte-for-byte. CI checks aggregate receipts without private
data. Tests verify exact subset selection, source preservation, separation of blind
payloads, blank initial decisions, approval provenance and sample-bound exports.
""".encode()


def build(root: Path, research_path: Path) -> tuple[dict, dict]:
    accepted = check_acceptance(root, local=True)
    parent = json.loads((root / "reports/reconciliation-audit.json").read_text())
    development = load_packet(root, parent, "development")
    holdout = load_packet(root, parent, "holdout")
    latest = json.loads(bound(root, accepted, f"{ACCEPTED_DIRECTORY}/latest-decisions.json"))
    approved = {row["pair_id"] for row in latest["decisions"]}
    selected = {
        "development": copy.deepcopy(development["cases"][60:]),
        "holdout": copy.deepcopy(holdout["cases"][:33]),
    }
    if any(len(selected[part]) != count for part, count in PARTS.items()):
        raise ValueError("Frozen remaining-case counts changed")
    ids = [c["pair_id"] for cases in selected.values() for c in cases]
    if len(set(ids)) != 60 or approved.intersection(ids):
        raise ValueError("Next batch repeats a comparison or an already approved case")
    raw_research = research_path.read_bytes()
    proposals = validate_research(json.loads(raw_research), selected["development"])
    for case, proposal in zip(selected["development"], proposals, strict=True):
        case["research"] = proposal
    ensure_blind(selected["holdout"])
    if sanitize_cases(selected["holdout"], "holdout") != selected["holdout"]:
        raise ValueError("Holdout source records changed under blind sanitization")
    names = json.loads((root / "data/review/linkage/sample.json").read_text())["source_names"]
    outputs = {f"{DIRECTORY}/research-input.json": raw_research}
    packets = {}
    for part, parent_sample in (("development", development), ("holdout", holdout)):
        sample = {
            "schema_version": "validation-review-v1",
            "mode": part,
            "batch_id": f"next60-v1-{part}",
            "parent_sample_sha256": parent_sample["sample_sha256"],
            "cases": selected[part],
        }
        if part == "development":
            sample["research_sha256"] = digest(raw_research)
        sample["sample_sha256"] = sample_digest(sample)
        prefix = f"{DIRECTORY}/{part}"
        outputs[prefix + ".json"] = json_bytes(sample)
        outputs[prefix + "-blank.csv"] = blank_csv(selected[part], sample["sample_sha256"])
        renderer = development_page if part == "development" else holdout_page
        outputs[prefix + ".html"] = renderer(selected[part], sample["sample_sha256"], names)
        packets[part] = {"cases": len(selected[part]), "sample_sha256": sample["sample_sha256"]}
    summary = research_summary(proposals)
    outputs[f"{DIRECTORY}/analysis.html"] = analysis_page(selected["development"], summary)
    outputs[f"{DIRECTORY}/index.html"] = index_page()
    receipt = {
        "schema_version": "next60-review-v1",
        "status": "next_60_pending_human_review",
        "accepted_receipt_sha256": sha256(root / ACCEPTED_RECEIPT),
        "parent_receipt_sha256": sha256(root / "reports/reconciliation-audit.json"),
        "parent_samples": {
            "development": development["sample_sha256"],
            "holdout": holdout["sample_sha256"],
        },
        "selection": "Frozen development [60:87] plus holdout [0:33]; original order retained.",
        "packets": packets,
        "research": summary,
        "new_human_labels_created": 0,
        "holdout_recommendations": 0,
        "unpresented_holdout": 47,
        "model_fitted": False,
        "auto_merges": 0,
        "formal_gold_set_complete": False,
        "implementation": implementation_hashes(root),
    }
    outputs[REPORT] = render_report(receipt)
    receipt["artifacts"] = {
        name: {"sha256": digest(content), "bytes": len(content)}
        for name, content in sorted(outputs.items())
    }
    if research_path.read_bytes() != raw_research:
        raise ValueError("Research changed during preparation")
    validate_receipt(receipt)
    return outputs, receipt


def validate_receipt(receipt: dict) -> None:
    summary = receipt["research"]
    if (
        receipt["schema_version"] != "next60-review-v1"
        or receipt["status"] != "next_60_pending_human_review"
        or {part: value["cases"] for part, value in receipt["packets"].items()} != PARTS
        or summary["case_count"] != 27
        or summary["independently_reviewed"] != 27
        or sum(summary["recommendation_counts"].values()) != 27
        or sum(summary["confidence_counts"].values()) != 27
        or sum(summary["second_review_outcomes"].values()) != 27
        or receipt["new_human_labels_created"] != 0
        or receipt["holdout_recommendations"] != 0
        or receipt["unpresented_holdout"] != 47
        or receipt["model_fitted"] is not False
        or receipt["auto_merges"] != 0
        or receipt["formal_gold_set_complete"] is not False
        or set(receipt["artifacts"]) != {REPORT, *(f"{DIRECTORY}/{n}" for n in PRIVATE_NAMES)}
    ):
        raise ValueError("Invalid next-60 aggregate receipt")


def prepare(root: Path, research_path: Path | None = None) -> dict:
    outputs, receipt = build(root, research_path or root / DIRECTORY / "research-input.json")
    outputs[RECEIPT] = json_bytes(receipt)
    with tempfile.TemporaryDirectory(prefix=".next60-", dir=root / "data") as temporary:
        staged = []
        for name, content in outputs.items():
            path = Path(temporary) / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
            staged.append((path, root / name))
        accept_outputs(staged)
    return receipt


def check(root: Path, *, local=False) -> dict:
    check_acceptance(root)
    receipt = json.loads((root / RECEIPT).read_text())
    parent = json.loads((root / "reports/reconciliation-audit.json").read_text())
    validate_receipt(receipt)
    if (
        receipt["implementation"] != implementation_hashes(root)
        or receipt["accepted_receipt_sha256"] != sha256(root / ACCEPTED_RECEIPT)
        or receipt["parent_receipt_sha256"] != sha256(root / "reports/reconciliation-audit.json")
        or receipt["parent_samples"]
        != {part: parent["packets"][part]["sample_sha256"] for part in PARTS}
        or bound(root, receipt, REPORT) != render_report(receipt)
    ):
        raise ValueError("Next-batch code, report or parent changed")
    if local:
        outputs, rebuilt = build(root, root / DIRECTORY / "research-input.json")
        if rebuilt != receipt:
            raise ValueError("Rebuilt next-batch receipt changed")
        for name, content in outputs.items():
            if (root / name).read_bytes() != content:
                raise ValueError(f"Next-batch artifact changed: {name}")
    return receipt


def verify_labels(root: Path, path: Path, part: str, *, complete=False) -> dict:
    if part not in PARTS:
        raise ValueError("Choose development or holdout")
    receipt = check(root)
    sample = json.loads(bound(root, receipt, f"{DIRECTORY}/{part}.json"))
    if sample["sample_sha256"] != receipt["packets"][part]["sample_sha256"]:
        raise ValueError("Wrong next-batch sample")
    return validate_export(path, sample, complete=complete)
