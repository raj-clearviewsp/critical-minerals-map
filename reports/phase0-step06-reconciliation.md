# Critical Minerals Atlas — review reconciliation and validation preparation

**Status: completed pilot imported; source evidence and formal review design prepared. Human adjudication and validation remain open.**

## Preserved review history

The original export and completed pilot are immutable. The append-only ledger has **40 events across 34 unique pairs**. Six pairs were reviewed twice: five answers were unchanged and one was refined to containment. The latest-answer view is derived separately; original reviewer text, notes, IDs and sample hashes are retained.

| Completed pilot answer | Count |
| --- | ---: |
| Same physical site | 14 |
| Distinct sites | 2 |
| A contains B | 1 |
| Related scope unclear | 2 |
| Insufficient evidence | 1 |

Completed CSV SHA-256: `faf5585e0813d048e59ca88fff68c8dc691bde67b69aed77cb7536557d3ec8cd`.

The three likely-match cases received same-site answers. The likely-distinct sample received two distinct-site answers and one same-site answer. This targeted sample diagnoses disagreements; it is not a population accuracy estimate. Follow-up government and filed technical-report evidence distinguishes the deposits in the contested likely-distinct case. The user/source conflict therefore does **not** establish that the distance rule made a false negative.

## Source research and the next review

Primary-source research covers **13 cases**, with **30 cited findings**. The private evidence packet includes direct links, dates where known, page or section locators and remaining questions. Indexed extracts are identified in the retained evidence where full primary documents could not be opened.

- **7 cases** have findings that could materially change an answer. Their new decisions start blank, with the prior answer shown read-only.
- **5 cases** still lack an exact source-ID or boundary crosswalk. They remain research items rather than another request to judge the same insufficient evidence.
- **1 case** supports the latest containment interpretation.

The evidence distinguishes individual deposits from districts, mine components from publisher groupings, and explicit identity aliases from shared parent-area names. These distinctions are attached to the exact reviewed records. Research proposals have not been promoted into human labels, global alias rules or canonical merges. Existing numerical triage thresholds remain frozen pending the formal review. No 45% exclusion or validated automatic-match rate is claimed.

## Frozen 200-comparison design

The design contains **200 distinct comparison units** after consolidating verified publisher views. **33 historical site comparisons** remain in development. One previously reviewed direct publisher-view duplicate remains in the provenance ledger and does not consume a site-identity validation case. There are **167 new comparisons**.

| Split | Total | Existing comparisons | New review cases |
| --- | ---: | ---: | ---: |
| Development | 120 | 33 | 87 |
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
