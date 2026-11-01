"""Aggregate public checkpoint and a private, local review entry page."""

# ruff: noqa: E501 -- Markdown/HTML paragraphs retain their natural line structure.
from __future__ import annotations

import html

from pipeline.triage.review_page import _STYLE, _digest


def render_report(receipt: dict) -> bytes:
    ledger, research, selection = (receipt[key] for key in ("ledger", "research", "selection"))
    return f"""# Critical Minerals Atlas — review reconciliation and validation preparation

**Status: completed pilot imported; source evidence and formal review design prepared. Human adjudication and validation remain open.**

## Preserved review history

The original export and completed pilot are immutable. The append-only ledger has **{ledger["history_events"]} events across {ledger["unique_reviewed_pairs"]} unique pairs**. Six pairs were reviewed twice: five answers were unchanged and one was refined to containment. The latest-answer view is derived separately; original reviewer text, notes, IDs and sample hashes are retained.

| Completed pilot answer | Count |
| --- | ---: |
| Same physical site | 14 |
| Distinct sites | 2 |
| A contains B | 1 |
| Related scope unclear | 2 |
| Insufficient evidence | 1 |

Completed CSV SHA-256: `{ledger["completed_csv_sha256"]}`.

The three likely-match cases received same-site answers. The likely-distinct sample received two distinct-site answers and one same-site answer. This targeted sample diagnoses disagreements; it is not a population accuracy estimate. Follow-up government and filed technical-report evidence distinguishes the deposits in the contested likely-distinct case. The user/source conflict therefore does **not** establish that the distance rule made a false negative.

## Source research and the next review

Primary-source research covers **{research["researched_cases"]} cases**, with **{research["findings"]} cited findings**. The private evidence packet includes direct links, dates where known, page or section locators and remaining questions. Indexed extracts are identified in the retained evidence where full primary documents could not be opened.

- **{research["disposition_counts"].get("human_adjudication", 0)} cases** have findings that could materially change an answer. Their new decisions start blank, with the prior answer shown read-only.
- **{research["disposition_counts"].get("research_open", 0)} cases** still lack an exact source-ID or boundary crosswalk. They remain research items rather than another request to judge the same insufficient evidence.
- **{research["disposition_counts"].get("corroborated", 0)} case** supports the latest containment interpretation.

The evidence distinguishes individual deposits from districts, mine components from publisher groupings, and explicit identity aliases from shared parent-area names. These distinctions are attached to the exact reviewed records. Research proposals have not been promoted into human labels, global alias rules or canonical merges. Existing numerical triage thresholds remain frozen pending the formal review. No 45% exclusion or validated automatic-match rate is claimed.

## Frozen 200-comparison design

The design contains **200 distinct comparison units** after consolidating verified publisher views. **{selection["retained_historical_units"]} historical site comparisons** remain in development. One previously reviewed direct publisher-view duplicate remains in the provenance ledger and does not consume a site-identity validation case. There are **{selection["new_units"]} new comparisons**.

| Split | Total | Existing comparisons | New review cases |
| --- | ---: | ---: | ---: |
| Development | 120 | {selection["retained_historical_units"]} | {receipt["packets"]["development"]["cases"]} |
| Holdout | 80 | 0 | 80 |

Selection uses fixed routing-category quotas and seeded diversity across source pairs, mineral families and distance bands. All historical cases are development-only. The split isolates whole connected candidate neighborhoods: no selected neighborhood or verified source view crosses development and holdout. Missed links outside the original block remain a possible leakage source.

The holdout review JSON and HTML omit machine routing, scores, explanations, previous answers and research recommendations. The development view also omits automated routing. New answers are blank. A separate manifest retains design strata for later audit.

This is a deliberately stratified diversity sample, **not a probability sample**. Report evaluation denominators and stratum coverage; do not extrapolate an unweighted accuracy estimate to all candidate pairs. All three existing likely-match units were already exposed and remain in development, so this design has no independent test of that existing heuristic's precision. A future model's predicted-positive set may differ.

## Review and evaluation protocol

1. Adjudicate the seven cited findings first. Record a source or rationale when changing an earlier decision. All prior events remain available.
2. Review the new development and blinded holdout packets. Previously reviewed development cases are carried forward, with research/adjudication flags retained.
3. Keep unresolved relationships visible and report abstention/coverage. Do not turn an uncertain answer into a binary match/nonmatch. Directed containment is a separate relationship; it is not same-site identity.
4. Complete and freeze the 200-case reference set before model tuning, as required by the implementation spec. Tune using development only. Evaluate the holdout after thresholds are frozen, report precision/recall with counts and uncertainty, and keep the spec's 97% auto-merge precision gate open until measured.
5. Audit clusters, candidate blocking omissions, stable IDs and publication constraints before accepting Step 6. Step 7 remains gated.

## Files and verification

Open `data/review/reconciliation-v1/index.html` for the local workspace. Its seven-case evidence review, new development packet and holdout packet export separate sample-bound CSV files. Validate an export with `python -m pipeline.reconciliation --labels /absolute/path/to/export.csv --packet adjudication` (or `development` / `holdout`). This checks the export without changing decisions.

`make reconcile-check` rebuilds the local packet and verifies its exact bytes. `make reconcile-receipt-check` verifies the public aggregate receipt, code hashes and report without private source data. Reruns refuse conflicting accepted outputs and preserve unchanged modification times.

Human/source records and detailed research remain Git-ignored. Public files contain aggregates, policy and checksums. **No model is fitted; no automatic merges, canonical IDs, new human labels or published observations are created.**
""".encode()


def render_index(receipt: dict, evidence: dict) -> bytes:
    esc = html.escape
    open_cases = [case for case in evidence["cases"] if case["disposition"] == "research_open"]
    findings = [case for case in evidence["cases"] if case["disposition"] == "human_adjudication"]

    def items(cases):
        return "".join(
            f"<li><strong>{esc(' / '.join(c['names']))}</strong><p>{esc(c['confidence_reason'])}</p></li>"
            for c in cases
        )

    style = (
        _STYLE
        + "\nmain{max-width:1080px;margin:auto;padding:24px 32px}header{max-width:1080px;margin:auto;padding:32px}h1{font-size:40px}h2{font-family:Georgia,serif;font-size:28px;margin-top:40px}.review-link{display:inline-block;background:#36584d;color:white;padding:14px 20px;text-decoration:none;font-size:19px}.case-list li{margin:22px 0}.case-list p{margin:8px 0}.packets{display:grid;grid-template-columns:1fr 1fr;gap:28px}.packets article{border-top:1px solid #bbb;padding-top:20px}.muted{color:#526159}@media(max-width:760px){.packets{grid-template-columns:1fr}main,header{padding:20px}}"
    )
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta name="referrer" content="no-referrer"><meta http-equiv="Content-Security-Policy" content="default-src 'none';style-src 'sha256-{_digest(style)}';base-uri 'none';form-action 'none'"><title>Critical Minerals Atlas — review workspace</title><style>{style}</style></head><body>
<header><p class="wordmark">Critical Minerals Atlas</p><h1>Your review, with better evidence</h1><p>Your completed pilot is saved. Both rounds remain intact: 40 answers across 34 unique comparisons.</p></header>
<main><h2>Start with the source findings</h2><p>Seven cases have new citations that could change an earlier answer. Each shows your previous decision, the source evidence and a separate research proposal. Your new answer starts blank.</p>
<p><a class="review-link" href="adjudication.html">Review the seven findings</a></p>
<details><summary>Which cases need attention?</summary><ul class="case-list">{items(findings)}</ul></details>
<h2>Five questions still need source research</h2><p>These remain unresolved because an exact boundary, source identifier or membership link is missing. No repeated vote is requested on the same incomplete evidence.</p>
<details><summary>See the open research questions</summary><ul class="case-list">{items(open_cases)}</ul></details>
<h2>The formal validation set</h2><p>The 200-comparison design retains 33 of your earlier site comparisons and adds 167 new ones. One earlier publisher-view duplicate remains in the history but does not need an identity vote. The seven findings above belong to the retained comparisons.</p>
<div class="packets"><article><h3>Development review</h3><p>87 new comparisons, grouped separately from the retained decisions. Source locations and classifications are shown without automated routing suggestions.</p><p><a href="development.html">Open development review</a></p></article>
<article><h3>Blinded validation</h3><p>80 fresh comparisons reserved for independent evaluation. Previous answers, research proposals and automated routing are omitted.</p><p><a href="holdout.html">Open blinded validation</a></p></article></div>
<h2>Save each review separately</h2><p>Enter your reviewer name and export progress as CSV. Each export carries its packet identity. Browser storage is convenient, but an exported file is your backup.</p>
<p class="muted">The pilot has not established a safe automatic-merge rate. Model tuning waits for the formal review; source research proposals never replace your answers automatically.</p>
</main></body></html>""".encode()
