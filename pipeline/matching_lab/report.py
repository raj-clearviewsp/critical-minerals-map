"""Public aggregate evidence for a private, explicitly unreleased experiment."""

from __future__ import annotations

import csv
import io
import json
from collections import Counter
from pathlib import Path

from .core import (
    DIRECTORY,
    ENV_LOCK,
    PROTOCOL,
    bound,
    digest,
    encoded,
    evaluate_agent,
    evaluate_human,
    load_frozen,
    save,
    sha,
)

RECEIPT = "reports/matching-experiment-audit.json"
REPORT = "reports/matching-experiment.md"


def implementation(root: Path) -> dict:
    paths = [
        *sorted((root / "pipeline/matching_lab").glob("*.py")),
        root / ENV_LOCK,
        root / PROTOCOL,
        root / "pipeline/link/candidates.py",
    ]
    return {p.relative_to(root).as_posix(): sha(p) for p in paths}


def percent(value) -> str:
    return "undefined (no eligible predictions)" if value is None else f"{100 * value:.1f}%"


def interval(value) -> str:
    return "undefined" if value is None else f"{100 * value[0]:.1f}%–{100 * value[1]:.1f}%"


def render(receipt: dict) -> bytes:
    d, h, a, s = (
        receipt["development_cv"],
        receipt["human33"],
        receipt["agent47"],
        receipt["stress"],
    )
    rows = []
    for label, m in [
        ("Development, grouped cross-validation", d),
        ("Blind human 33", h),
        ("Assisted agent 47 agreement", a),
    ]:
        rows.append(
            f"| {label} | {m['cases']} | {m['resolved_identity_cases']} | {m['abstentions']} | "
            f"{m['tp']} / {m['tp'] + m['fp']} | {percent(m['recall_within_resolved_sample'])} |"
        )
    changes = "\n".join(
        f"- {key}: {v['route_changes']} of {v['cases']} route changes; largest score shift "
        f"{v['largest_score_change']:.3f}."
        for key, v in s["perturbations"].items()
    )
    variants = "\n".join(
        f"| Omit {key} | {value['tp']} | {value['fp']} | "
        f"{percent(value['recall_within_resolved_sample'])} |"
        for key, value in receipt["ablations"].items()
    )
    sensitivity = "\n".join(
        f"| {key} | {value['tp']} | {value['fp']} | "
        f"{percent(value['recall_within_resolved_sample'])} |"
        for key, value in receipt["prior_sensitivity"].items()
    )
    return f"""# Matching experiment: tested, automatic merging not released

**The 97% automatic-merge precision gate is not demonstrated.** The frozen matcher
scored {receipt["candidate_pairs"]:,} candidate pairs. Scope and evidence guards
refer most pairs for review. On the 33 blind human cases it proposed {h["tp"] + h["fp"]}
automatic matches among {h["resolved_identity_cases"]} resolved comparisons.
Its automatic-match precision is {percent(h["precision"])}; this cannot be called a
successful precision test. No canonical records or published data were changed.

## What was fitted, and what stayed separate

The owner explicitly requested research for the final 47 and experimental matching
tests on 2026-11-04. This changes their role from blind holdout to assisted audit.
The original 200-human-review requirement is still incomplete: 153 formal units have
human answers; 47 agent recommendations await approval. The 33 earlier holdout answers
were collected blind and were opened for evaluation only after model/settings freeze.

Training uses only the 120 development units: 30 same-site, 37 distinct-site, 8 directed
containment, 45 unresolved. Containment is nonidentity; unresolved answers are excluded
from likelihood estimation and remain explicit abstentions in evaluation.

RapidFuzz compares trusted source names/aliases after Unicode normalization. The
model uses name, distance, entity scope and mapped geology categories. A supervised
Fellegi–Sunter estimate uses Laplace smoothing; Splink computes scores from those
fixed parameters. Exact candidate-pair blocking in the adapter preserves the original
comparison universe. No agent research text, recommendation, old routing score or
holdout label enters a feature or fit. No held-out outcome selects a variant.

The .95/.70 routing thresholds were declared before evaluation. Parent/component,
publisher-cluster, unknown/qualified scope, processing, country, commodity, numeric
and directional guards override a high score to review. Scores are conditional on
the deliberately selected training sample; they are **not calibrated population
probabilities**. Source fields can be dependent and sparse.

## Results and denominators

| Evaluation set | Cases | Resolved | Abstentions | Correct / proposed¹ | Auto-match recall |
| --- | ---: | ---: | ---: | ---: | ---: |
{chr(10).join(rows)}

¹ Auto-match denominator includes resolved comparisons only. Proposed automatic
matches whose labels remain unresolved: development {d["unresolved_auto_candidates"]},
blind human {h["unresolved_auto_candidates"]}, assisted agent {a["unresolved_auto_candidates"]}.

Development cross-validation keeps whole connected candidate neighborhoods in one
of five folds and refits without the held-out fold. Its precision interval is
{interval(d["precision_wilson95"])}. Three correct proposals do not substantiate 97%.
The blind-human precision interval is {interval(h["precision_wilson95"])}.
These Wilson intervals are descriptive binomial intervals; they do not correct for
selection bias, source dependence or neighborhood correlation. This stratified
diversity sample does not support a population-wide accuracy claim.

The raw score alone exceeds .95 on {h["raw_score_tp"] + h["raw_score_fp"]} resolved blind
pairs ({h["raw_score_tp"]} same-site and {h["raw_score_fp"]} nonidentity). It also exceeds
.95 on {receipt["human_raw_unresolved_high"]} unresolved blind pairs. The experimental
scope guards remain in place; high scores do not settle those missing definitions.
Agent 47 numbers measure agreement with proposals, **not independent human accuracy**.

## Robustness checks

- Reversed all {s["pair_reversal_cases"]:,} pairs:
  {s["pair_reversal_failures"]} feature/guard mismatches.
- Reordered all 200 benchmark rows: largest score difference {s["row_reorder_largest_delta"]:.3g}.
- Every Splink score is checked against an independent log-likelihood implementation.
{changes}

Stable routing under coordinate perturbations does not imply accurate scores:
the guards frequently dominate the result. Removing aliases changes little here
because primary names or unresolved-scope guards already drive these cases.

All {receipt["candidate_pairs"]:,} candidates produced exactly one finite prediction.
Routes: {json.dumps(receipt["full_candidate_routes"], sort_keys=True)}.
Unresolved entity scope triggers a review guard on
{receipt["guard_counts"].get("unresolved_entity_scope", 0):,} pairs; multiple guards can
apply to the same pair. This is the dominant reason the matcher refers so much work.
The {s["cluster_counts"]["proposed_edges"]} eligible high-score edges form
{s["cluster_counts"]["proposed_components"]} proposed components; the largest has
{s["cluster_counts"]["largest_component"]} records. Every pair within each component
was checked for guard/score consistency. {s["cluster_counts"]["quarantined_components"]}
components were quarantined. This is a structural audit, not factual approval; actual
merges remain zero.

A separate wider-block check found {s["blocking_counts"]["missing_candidates"]} exact
trusted-name/shared-mineral pairs within 25 km absent from the original block,
including {s["blocking_counts"]["missing_cross_source"]} across sources and
{s["blocking_counts"]["missing_within10km"]} within 10 km. These are potential candidates,
not proven missed matches. Blocking recall remains unmeasured, especially for different
names. No large name groups were skipped: {s["blocking_counts"]["skipped_large_name_groups"]}.

## Fixed sensitivity checks, development only

| Grouped CV variant | Correct proposals | Wrong proposals | Auto-match recall |
| --- | ---: | ---: | ---: |
{variants}

| Assumed prior, in-sample diagnostic | Correct proposals | Wrong proposals | Auto-match recall |
| --- | ---: | ---: | ---: |
{sensitivity}

These variants were predefined diagnostics and did not replace the frozen model.
Unit/integration tests additionally cover null/generic names, alias roles, non-Latin
text, coordinate validity and the antimeridian, threshold boundaries, exact label
alignment, exclusion of abstentions, holdout access isolation, transitive-chain
quarantine, and immutable file writes.

## Consequence for the site

Keep the source-record explorer available and keep automatic consolidation disabled.
The main bottleneck is documented entity scope, not a lack of fuzzy-name scoring.
The final 47 dossier exposes the evidence needed to make specific decisions. Future
improvements should add source-supported scope/alias crosswalks and a documented
broader candidate audit. Any revision informed by these test errors needs a new
evaluation or must be reported as exploratory; the 33 cannot be reused as untouched
holdout after tuning to their outcomes. No additional review batch was
created by this experiment.

## Reproduction

The matching environment has its own committed lockfile, preserving the earlier
pipeline lock and accepted receipts. Versions: {json.dumps(receipt["versions"], sort_keys=True)}.
The imported name-normalization and distance helpers are separately verified against
their pre-existing accepted linkage receipt; their code remained unchanged throughout.
Private model, predictions, development/CV results, human evaluation, assisted audit
and stress details live under `{DIRECTORY}/`. Public artifacts contain aggregates and
checksums only. The source files, code and protocol are checksum-bound; conflicting
reruns refuse to overwrite existing outputs.

References: [Splink comparisons](https://moj-analytical-services.github.io/splink/topic_guides/comparisons/customising_comparisons.html),
[Splink probability configuration](https://moj-analytical-services.github.io/splink/api_docs/comparison_library.html),
[Splink evaluation](https://moj-analytical-services.github.io/splink/api_docs/evaluation.html),
[NIST Wilson intervals](https://www.itl.nist.gov/div898/handbook/prc/section2/prc241.htm).
""".encode()


def prepare(root: Path) -> dict:
    frozen, values = load_frozen(root)
    directory = root / DIRECTORY
    for name in ("human-evaluation.json", "agent-audit.json", "stress-audit.json"):
        if not (directory / name).is_file():
            raise ValueError("Complete the frozen evaluations before report preparation")
    human = evaluate_human(root)
    agent = evaluate_agent(root, root / "data/review/final47-v1/research-input.json")
    stress = json.loads((directory / "stress-audit.json").read_text())
    final = json.loads((root / "reports/final47-audit.json").read_text())
    if (
        agent["research_sha256"]
        != final["artifacts"]["data/review/final47-v1/research-input.json"]["sha256"]
    ):
        raise ValueError("Agent audit differs from accepted research")
    for report in (human, agent, stress):
        if report["freeze_sha256"] != sha(directory / "freeze.json"):
            raise ValueError("Evaluation used another model freeze")
    raw = bound(
        root, "reports/next60-completed-audit.json", "data/review/next60-completed-v1/holdout.csv"
    )
    if digest(raw) != human["labels_sha256"]:
        raise ValueError("Human evaluation is bound to another submitted export")
    submitted = list(csv.DictReader(io.StringIO(raw.decode("utf-8-sig"))))
    old_linkage = json.loads((root / "reports/linkage-review-audit.json").read_text())
    helper = "pipeline/link/candidates.py"
    if sha(root / helper) != old_linkage["implementation"][helper]:
        raise ValueError("Imported matching helpers changed from the original accepted code")
    high_unresolved = sum(
        r["label"] in {"uncertain", "related_scope_unclear", "insufficient_evidence"}
        and values["predictions.json"][r["pair_id"]]["score"] >= 0.95
        for r in submitted
    )
    dev = values["development-evaluation.json"]
    receipt = {
        "schema_version": "matching-experiment-audit-v1",
        "status": "automatic_merge_gate_not_demonstrated",
        "model_frozen_sha256": sha(directory / "freeze.json"),
        "source_helper_receipt_sha256": sha(root / "reports/linkage-review-audit.json"),
        "completed_receipt_sha256": sha(root / "reports/next60-completed-audit.json"),
        "research_receipt_sha256": sha(root / "reports/final47-audit.json"),
        "candidate_pairs": frozen["candidate_pairs"],
        "full_candidate_routes": frozen["full_candidate_routes"],
        "guard_counts": dict(
            sorted(Counter(g for row in values["features.json"] for g in row["guards"]).items())
        ),
        "versions": frozen["versions"],
        "model_implementation": frozen["implementation"],
        "implementation": implementation(root),
        "development_cv": dev["group_cross_validation"],
        "human33": human["evaluation"],
        "human_raw_unresolved_high": high_unresolved,
        "agent47": agent["agreement"],
        "ablations": dev["leave_one_feature_out_group_cv"],
        "prior_sensitivity": dev["fixed_prior_sensitivity_in_sample"],
        "stress": {
            **{k: v for k, v in stress.items() if k not in {"cluster_audit", "blocking_audit"}},
            "cluster_counts": {
                k: v for k, v in stress["cluster_audit"].items() if k != "components"
            },
            "blocking_counts": {k: v for k, v in stress["blocking_audit"].items() if k != "pairs"},
        },
        "gate_passed": False,
        "actual_merges": 0,
        "canonical_rows_added": 0,
        "formal_human_gold_complete": False,
        "trained_on_holdout": False,
        "trained_on_agent47": False,
    }
    report = render(receipt)
    receipt["artifacts"] = {
        str(p.relative_to(root)): {"sha256": sha(p), "bytes": p.stat().st_size}
        for p in sorted(directory.glob("*.json"))
    }
    receipt["artifacts"][REPORT] = {"sha256": digest(report), "bytes": len(report)}
    save(root, {REPORT: report, RECEIPT: encoded(receipt)})
    return receipt


def check(root: Path, *, local=False) -> dict:
    receipt = json.loads((root / RECEIPT).read_text())
    if (
        receipt["schema_version"] != "matching-experiment-audit-v1"
        or receipt["status"] != "automatic_merge_gate_not_demonstrated"
        or receipt["implementation"] != implementation(root)
        or receipt["gate_passed"] is not False
        or receipt["actual_merges"] != 0
        or receipt["canonical_rows_added"] != 0
        or receipt["formal_human_gold_complete"] is not False
        or receipt["trained_on_holdout"] is not False
        or receipt["trained_on_agent47"] is not False
        or receipt["human33"]["cases"] != 33
        or receipt["agent47"]["cases"] != 47
        or receipt["development_cv"]["cases"] != 120
        or receipt["completed_receipt_sha256"] != sha(root / "reports/next60-completed-audit.json")
        or receipt["research_receipt_sha256"] != sha(root / "reports/final47-audit.json")
        or receipt["source_helper_receipt_sha256"]
        != sha(root / "reports/linkage-review-audit.json")
    ):
        raise ValueError("Matching experiment receipt or implementation changed")
    report = render(receipt)
    if (root / REPORT).read_bytes() != report or receipt["artifacts"][REPORT] != {
        "sha256": digest(report),
        "bytes": len(report),
    }:
        raise ValueError("Matching aggregate report changed")
    if local:
        for name, meta in receipt["artifacts"].items():
            p = root / name
            if {"sha256": sha(p), "bytes": p.stat().st_size} != meta:
                raise ValueError("Private matching artifact changed")
        if prepare(root) != receipt:
            raise ValueError("Matching aggregate rebuild changed")
    return receipt


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--local", action="store_true")
    arguments = parser.parse_args()
    result = (
        check(arguments.root, local=arguments.local) if arguments.check else prepare(arguments.root)
    )
    print(json.dumps({key: result[key] for key in ("status", "candidate_pairs", "gate_passed")}))
