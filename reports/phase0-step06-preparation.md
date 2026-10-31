# Phase 0 · Step 6 review preparation

Run date: October 31, 2026, America/Los_Angeles.

**Status: awaiting human labels; Step 6 is not complete.** Step 5 commit `3206334` passed [CI run 41234601740](https://github.com/raj-clearviewsp/critical-minerals-map/actions/runs/41234601740) before this preparation began. This checkpoint prepares a reproducible local review packet. It fits no linkage model, creates no canonical site IDs and performs no merges.

## Concrete review packet

Open `data/review/linkage/index.html` in a browser. The page compares original names, coordinates, reported commodities/status and source provenance side by side. It has readable type, a mobile stacked layout, expandable evidence, progress navigation, notes and an explicit reviewer name. Every decision starts blank. Choose Same site, Different sites or Uncertain; export progress as a new CSV. Local browser storage is convenient but is not a backup.

The private folder also contains `sample.json`, the original `labels-blank.csv`, the full `candidates.json`, and instructions. Keep these originals unchanged. Generated source-level details remain Git-ignored and out of public outputs because source reuse terms differ, including ICMM's redistribution restriction. The committed [audit receipt](linkage-review-audit.json) contains only source/category aggregates, implementation fingerprints and artifact checksums.

- **10,788** eligible point records from twelve sources.
- **39,148** unique candidate pairs.
- **200** unlabelled review pairs covering 382 distinct source records.
- **0** human labels, trained scores, auto-merges or canonical site rows.
- Sample SHA-256: `e9b54bf0479727ef7741aa790775ec601a6133347f67f6fbb6fde56899253738`.

## Scope and preserved uncertainty

| Source | Pilot rows | Eligible points | Excluded from candidates |
| --- | ---: | ---: | ---: |
| `aiddata_transition_v2` | 1,291 | 0 | 1,291 |
| `ga_critical_2025` | 246 | 246 | 0 |
| `ga_critical_2025_arcgis` | 246 | 246 | 0 |
| `ga_global_ree` | 376 | 365 | 11 |
| `gseu_crm` | 138 | 138 | 0 |
| `icmm` | 305 | 216 | 89 |
| `jasansky` | 103 | 66 | 37 |
| `oecd_export` | 66,693 | 0 | 66,693 |
| `usgs_global_critical` | 264 | 254 | 10 |
| `usgs_global_ree` | 3,929 | 3,080 | 849 |
| `usgs_laterites` | 120 | 120 | 0 |
| `usgs_mcs_2026` | 515 | 0 | 515 |
| `usgs_mrds` | 4,701 | 4,533 | 168 |
| `usgs_us_critical` | 142 | 142 | 0 |
| `wapha` | 1,382 | 1,382 | 0 |
| **Total** | **80,451** | **10,788** | **69,663** |

Only deposits and explicitly identified mines enter this point review. Country/policy and financing observations, source groupings, standalone processing facilities, mixed mine/processing complexes and unsuitable geometries stay in their original pilot files. Exclusion counts and reasons reconcile for every source. In particular, 46 mixed mine/processing records need component-level scope review; 34 tailings/processing-typed REE records and ten explicitly marine crust/nodule records do not enter the terrestrial mine/deposit sample. Generic shoreline placers and ancient sea-floor volcanic deposits are not guessed into deep-sea scope.

All 10,788 eligible points have unknown source precision. That remains null; the review page says “Not stated by source.” A conservative 10 km radius generates review candidates only and does not establish eligibility for automatic merging. A later linkage policy must handle the uncertainty explicitly. Unlocated records are retained in staging, never assigned invented coordinates. No country is inferred from coordinates. Same-source pairs and country-disagreement pairs remain available for review.

## Reproducible blocking and sampling

A three-dimensional unit-sphere grid finds nearby points without dateline or polar seams. Exact great-circle distance and shared REE/Ni/Co family gates follow. The helper supports 10 km exact/approximate and 25 km district precision, using the smaller pair radius; the actual snapshot uses only the explicit unknown-precision 10 km review policy.

Sampling categories use normalized names/aliases with Unicode case/diacritic folding and generic mine/deposit/project words removed. ICMM's documented semicolon-separated aliases are split for comparison while the original cell is retained. No fuzzy match score, transliteration guess or model prediction is used for sample selection. RapidFuzz/Splink comparison and tuning remain for the subsequent labelled stage.

The seed `atlas-gold-v1` creates reproducible hash ordering within strata. Quotas first cover available category margins, then distribute remaining capacity using square-root stratum counts. The sample covers all twelve source categories, all 71 source-pair categories, all three families, all three observed distance bands and all five name categories. It includes 97 same-source pairs. These are diversity safeguards, not inferred labels.

There are **1,018 joint strata**, of which **158** are sampled and **860** have zero quota. Each sampled stratum records its count/quota weight; unsampled weights are null. Unweighted accuracy on this deliberately stratified sample is not population accuracy. Candidate-only labels cannot measure matches lost outside the blocking radius or exclusions. Future evaluation must disclose those limits, separate tuning from held-out evaluation, and report precision/recall without treating unrepresented strata as validated. No claim of the spec's 97% auto-merge precision is made here.

## Validation and next gate

All **432 Python tests pass**, with Ruff lint and formatting clean. Full local receipt verification passes. A second full preparation reproduces all six review artifacts without changing bytes or modification times; all 131 existing raw, staging, canonical, published-scaffold and parent-receipt paths remain unchanged. The matching pushed commit is subject to the repository’s three-job GitHub Actions gate.

The packet is deterministic and receipt-last accepted. An unchanged rerun preserves bytes and modification times; differing existing outputs are refused. Full local verification rebuilds source eligibility, candidates, sample, HTML and blank CSV from verified pilot inputs, then compares every artifact. Offline CI checks prior receipts, code fingerprints, source reconciliations, stratum identities, quotas, coverage and hashes; it does not claim to replay private datasets.

The CSV validator checks all 200 exact identities, source IDs, the immutable sample SHA, allowed labels and named reviewers. It rejects duplicate/missing/extra pairs, stale samples and changed record IDs. Partial exports can be checked with `--partial`; complete gold labels require all 200 to be decisively same-site or different-site. Uncertain labels remain unresolved. Validation neither edits decisions nor establishes their factual accuracy.

Desktop (1280 px) and mobile (390 px) browser checks show no horizontal overflow and correct two-column/stacked layouts. Navigation keeps labels blank. CSV export was exercised only on a separate synthetic two-pair page; no real pair was labelled during testing. The main Atlas preview remains the clean, readable design introduced in Step 3, with honest unpublished-data copy.

The implementation spec requires: **“Hand-label a stratified gold set of 200 pairs before tuning.”** Once the human review is complete, Step 6 can continue with comparison/model fitting, evaluation, review queues and persistent site IDs. Its full acceptance gate still requires at least 97% auto-merge precision, reported recall, exported review CSV, a pushed commit and green CI. Steps 7–12 remain in order behind that gate. No website or observation data has been deployed.
