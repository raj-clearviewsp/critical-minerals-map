# Phase 0 · Step 2 acquisition checkpoint

Run date: October 28, 2026, America/Los_Angeles. Raw snapshots use UTC date `2026-10-29`.

**Status: implementation tested; live acquisition partially complete. Step 3 has not started.** Step 1 passed remote CI before Step 2 began. Required bulk files remain in the [manual acquisition queue](../review/manual-acquisition.md).

## Captured data

Nine sources, 39 raw files, **190,625,345 bytes** (about 182 MiB). Every accepted snapshot passes the raw-manifest JSON schema, complete-file coverage, byte-count and SHA-256 checks. Raw files are local and Git-ignored; they have not been published or transformed. [Inventory CSV](acquisition-inventory.csv) includes source URLs, retrieval timestamps and evidence grades.

| Source | Files | Bytes | Raw record accounting |
| --- | ---: | ---: | --- |
| USGS U.S. critical deposits v3 | 4 | 1,056,714 | 1,271 object IDs; every requested ID returned exactly once |
| USGS Ni–Co laterites | 1 | 149,375 | CSV captured; row parsing deferred to Step 4 |
| GSEU European deposits | 1 | 4,215,978 | Workbook captured; row parsing deferred |
| Jasansky mine production | 1 | 1,077,424 | Original archive captured; tables not normalized |
| GA operating mines/deposits API | 12 | 252,146 | 538 object IDs across four source layers; all returned exactly once |
| ICMM v1.5 | 1 | 785,242 | Workbook captured under Raj's recorded approval |
| AidData transition minerals v2 | 1 | 3,172,579 | Archive captured; embedded ODC-By license verified |
| OECD export restrictions v3.0 | 17 | 179,809,674 | Full 2009–2024 inventory in 16 annual CSVs plus structure XML |
| USGS 2025 critical list | 1 | 106,213 | Official HTML retained for the Step 3 vocabulary |

Normalized rows: **0**. Published observation rows: **0**. Record rejects: not applicable until parsing; no input record has been silently discarded. Acquisition attempts that failed left no accepted partial snapshot. Source-row reconciliation and commodity filtering belong to Steps 4 and 5 and have not been performed early.

## Verification

- 89 Python tests pass; Ruff lint/format checks pass.
- All 18 registry files (17 spec entries plus the GA API variant) validate.
- Fresh empty-input pipeline passes against the complete registry in an isolated temporary root, preserving the real raw archive.
- Frontend: 3 manifest tests, Astro checks and static build pass; source data is not displayed by the scaffold.
- Cached reruns verified the captured files without mutation. GSEU and Jasansky were additionally fetched again from the publisher; byte-identical refreshes returned `unchanged`, retaining original manifests and retrieval timestamps.
- Raw manifests account for every captured file. Publisher checksums were also verified where supplied.
- ArcGIS uses bounded object-ID batches, validates complete ID sets and rejects truncation or mismatches. Multi-layer snapshots are accepted atomically only after all layers succeed.
- HTTP tests cover declared contact identity, robots rules including wildcard/Allow precedence, redirects, 429 and Retry-After dates, crawl delays, protected datasets, checksum failures and acquisition approvals.
- GitHub CI for this checkpoint will run on push; Step 2 completion is **not** asserted by green code checks alone.

## Access outcomes and fixes

- ScienceBase explicitly disallows automated acquisition: global REE, the global 2017 layer and MCS 2026 remain manual. Direct binary URLs were not guessed.
- The GA CloudFront workbook host explicitly disallows automated access; the public MapServer alternative was captured under its own CC BY 4.0 license.
- WAPHA's API file endpoint requires authentication; its public download path is robots-disallowed. No login or bypass attempted.
- MRDS bulk access remains an access-terms review item. A 403 from robots alone is not treated as a prohibition; the unresolved bulk-use terms are recorded separately.
- The initial standard-library robots parser handled Zenodo's wildcard Allow rule incorrectly. Replaced with Protego, added a regression test, then successfully captured the files without changing identity or bypassing policy.
- OECD's all-years response exceeded the 150 MiB per-file ceiling. Replaced it with the publisher-documented inclusive annual queries, preserving all years and dimensions. Failed large response was not accepted as raw data.

## Source decisions

Raj explicitly approved ICMM, AidData v2 and the GA workbook for a free project with clear attribution. The approval is versioned in `overrides/source_acquisition_approvals.yaml`. AidData's downloaded workbook independently confirms ODC-By in `License!A4`; the current registry records that grant while retaining the initial uncertainty as audit history. The original raw manifest remains unchanged and reflects what was known at retrieval. ICMM's actual website terms remain recorded. See [source decisions](../docs/source-decisions.md).

Mindat stays excluded. Official export announcements remain a per-document manual collection. No contacts were sent to publishers, no deployment occurred, and no monitoring automation was created.

## Remaining gate

Obtain the required original files through permitted manual downloads or publisher-approved access, register their provenance/checksums, and verify their reruns. Then finish Step 2's report/commit/CI gate before building vocabularies. Do not treat inaccessible sources as empty datasets or silently reduce the spec's coverage.
