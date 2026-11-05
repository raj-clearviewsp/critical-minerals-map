# Matching experiment: tested, automatic merging not released

**The 97% automatic-merge precision gate is not demonstrated.** The frozen matcher
scored 39,148 candidate pairs. Scope and evidence guards
refer most pairs for review. On the 33 blind human cases it proposed 0
automatic matches among 13 resolved comparisons.
Its automatic-match precision is undefined (no eligible predictions); this cannot be called a
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
| Development, grouped cross-validation | 120 | 75 | 45 | 3 / 3 | 10.0% |
| Blind human 33 | 33 | 13 | 20 | 0 / 0 | 0.0% |
| Assisted agent 47 agreement | 47 | 39 | 8 | 0 / 0 | 0.0% |

¹ Auto-match denominator includes resolved comparisons only. Proposed automatic
matches whose labels remain unresolved: development 0,
blind human 0, assisted agent 0.

Development cross-validation keeps whole connected candidate neighborhoods in one
of five folds and refits without the held-out fold. Its precision interval is
43.9%–100.0%. Three correct proposals do not substantiate 97%.
The blind-human precision interval is undefined.
These Wilson intervals are descriptive binomial intervals; they do not correct for
selection bias, source dependence or neighborhood correlation. This stratified
diversity sample does not support a population-wide accuracy claim.

The raw score alone exceeds .95 on 3 resolved blind
pairs (3 same-site and 0 nonidentity). It also exceeds
.95 on 0 unresolved blind pairs. The experimental
scope guards remain in place; high scores do not settle those missing definitions.
Agent 47 numbers measure agreement with proposals, **not independent human accuracy**.

## Robustness checks

- Reversed all 39,148 pairs:
  0 feature/guard mismatches.
- Reordered all 200 benchmark rows: largest score difference 1.11e-16.
- Every Splink score is checked against an independent log-likelihood implementation.
- jitter_0.001_degrees: 0 of 200 route changes; largest score shift 0.227.
- jitter_0.01_degrees: 0 of 200 route changes; largest score shift 0.362.
- punctuation_case: 0 of 200 route changes; largest score shift 0.000.
- remove_aliases: 0 of 200 route changes; largest score shift 0.000.

Stable routing under coordinate perturbations does not imply accurate scores:
the guards frequently dominate the result. Removing aliases changes little here
because primary names or unresolved-scope guards already drive these cases.

All 39,148 candidates produced exactly one finite prediction.
Routes: {"auto_candidate": 269, "identity_review": 24, "keep_separate": 1076, "scope_or_evidence_review": 37779}.
Unresolved entity scope triggers a review guard on
36,403 pairs; multiple guards can
apply to the same pair. This is the dominant reason the matcher refers so much work.
The 269 eligible high-score edges form
247 proposed components; the largest has
3 records. Every pair within each component
was checked for guard/score consistency. 0
components were quarantined. This is a structural audit, not factual approval; actual
merges remain zero.

A separate wider-block check found 245 exact
trusted-name/shared-mineral pairs within 25 km absent from the original block,
including 177 across sources and
0 within 10 km. These are potential candidates,
not proven missed matches. Blocking recall remains unmeasured, especially for different
names. No large name groups were skipped: 0.

## Fixed sensitivity checks, development only

| Grouped CV variant | Correct proposals | Wrong proposals | Auto-match recall |
| --- | ---: | ---: | ---: |
| Omit distance | 3 | 0 | 10.0% |
| Omit geology | 3 | 0 | 10.0% |
| Omit name | 0 | 0 | 0.0% |
| Omit scope | 3 | 0 | 10.0% |

| Assumed prior, in-sample diagnostic | Correct proposals | Wrong proposals | Auto-match recall |
| --- | ---: | ---: | ---: |
| 0.01 | 0 | 0 | 0.0% |
| 0.1 | 3 | 0 | 10.0% |
| 0.5 | 3 | 0 | 10.0% |

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
pipeline lock and accepted receipts. Versions: {"duckdb": "2.0.0", "pandas": "2.3.3", "rapidfuzz": "3.14.6", "splink": "4.0.17"}.
The imported name-normalization and distance helpers are separately verified against
their pre-existing accepted linkage receipt; their code remained unchanged throughout.
Private model, predictions, development/CV results, human evaluation, assisted audit
and stress details live under `data/review/matching-experiment-v1/`. Public artifacts contain aggregates and
checksums only. The source files, code and protocol are checksum-bound; conflicting
reruns refuse to overwrite existing outputs.

References: [Splink comparisons](https://moj-analytical-services.github.io/splink/topic_guides/comparisons/customising_comparisons.html),
[Splink probability configuration](https://moj-analytical-services.github.io/splink/api_docs/comparison_library.html),
[Splink evaluation](https://moj-analytical-services.github.io/splink/api_docs/evaluation.html),
[NIST Wilson intervals](https://www.itl.nist.gov/div898/handbook/prc/section2/prc241.htm).
