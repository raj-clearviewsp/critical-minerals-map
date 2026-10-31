# Changelog

## 2026.10.31 — Phase 0, Step 3 vocabulary review

- Added source-aware commodity, deposit-type and status vocabularies, ISO country definitions and explained country overrides.
- Added source-field terminology audits, retained source-format evidence, an unmapped-term queue and a reproducible coverage receipt. All 2,640 observed pilot field values and 589 explicit code expectations pass; full validation is recorded in the Step 3 report.
- Kept historical production, active labels, uncertain classifications, facility kinds and finance milestones distinct; vocabulary matching does not assign maturity or current operating status.
- Added `make vocab` for the full local-source audit and `make vocab-check` for retained-audit validation in CI without raw downloads.
- Refreshed the empty Astro preview with readable typography, straightforward navigation and a clear unpublished-data message; removed decorative numbering and implementation-oriented copy.
- Fixed the preview Make target to use the host already configured by the npm script.
- Step 3 completed with commit 8f0d02e and green CI run 41232185905. Step 4 began only after that gate. No data or site has been deployed.

## 2026.10.28 — Phase 0, Step 1 scaffold

- Added the specified repository layout and ordered Make targets.
- Added locked Python tooling, pytest, Ruff, pre-commit, and separate GitHub Actions jobs for checks, data and site.
- Added zero-row Phase 0 table contracts, deterministic CSV/Parquet outputs, checksummed build manifest and stage receipts.
- Added fail-closed guards against populated inputs, invalid sequencing, modified artifacts and unintended overwrites.
- Added a static Astro scaffold that clearly states no records have been loaded.
- No sources ingested, facts published, websites deployed or Phase 1 work started.

Remote verification is recorded in `reports/phase0-step01.md` and the matching GitHub Actions run.

## 2026.10.28 — Phase 0, Step 2 acquisition checkpoint

- Registered all 17 Phase 0 source entries plus the separately licensed GA ArcGIS variant, with source-specific evidence, access rules and fetch configurations.
- Added file and paginated ArcGIS fetchers, standards-aware robots parsing, rate/retry handling, raw manifests, publisher checksum checks, deterministic cache verification and refresh no-ops.
- Recorded Raj's explicit acquisition approvals for ICMM, AidData v2 and the GA workbook.
- Verified the embedded AidData release license is ODC-By 1.0 and updated the source decision accordingly.
- Documented source currency/coverage corrections and a manual acquisition queue for inaccessible bulk files.
- Raw files stay local and immutable; no observation data has been normalized or published. Step 2 remains incomplete until the required acquisition queue is resolved.

## 2026.10.31 — Browser acquisition and verified local import

- Reused the existing USGS global REE archive and downloaded nine remaining files through publisher browser controls, resolving all seven core manual-source entries.
- Organized immutable source/date folders with SHA-256 manifests and verified publisher checksums for MCS and WAPHA. Original Downloads files remain intact.
- Added tested local-import support with honest unknown retrieval times, separate registration times, atomic copies and unchanged reruns.
- Verified the GA 2025 workbook’s embedded CC BY 4.0 grant and updated its registry decision.
- Acquired the original MRDS compact relational export with commodity, names, materials and metadata tables.
- Kept normalization and later numbered steps untouched; no data or site deployed.

## 2026.10.31 — Phase 0, Step 4 source normalization

- Added fifteen source parsers with a strict, provenance-preserving Parquet staging contract and complete primary-table reconciliation.
- Preserved unlocated REE records, raw rank/size/status/facility fields, source geometry, Jasansky subsites and linked evidence. Kept country/policy and finance observations distinct from physical assets.
- Added stable source-content fallback identities, native/physical-row duplicate checks, explicit footnote and coordinate issue accounting, and independent release counts.
- Added bounded-memory parsing/writing, immutable-output conflict checks, unchanged reruns, failure rollback and local/CI receipt verification.
- Step 3 passed CI before normalization began; pilot filtering and subsequent steps remain gated on this commit’s checks. No public deployment.

## 2026.10.31 — Phase 0, Step 5 pilot selection

- Added exact, source-aware REE/Ni/Co selection with commodity roles, qualifiers, unresolved terms and host-country evidence appended to unchanged source records.
- Preserved MRDS linked commodity importance and source pointers; kept financial collateral and waste-only content out of selection. Explicit source-scope fallbacks remain labelled.
- Added source/mineral/country/kind counts, row reconciliation, full byte verification and human scope-review records for five conflicting REE-compilation entries.
- Step 4 passed CI before filtering began; linkage and subsequent steps remain gated on this commit’s checks. No canonical sites or published observations.

## 2026.10.31 — Phase 0, Step 6 review preparation

- Step 5 completed with commit 3206334 and green CI run 41234601740 before this work began.
- Added explicit mine/deposit point eligibility, exact spatial blocking and deterministic stratified sampling: 10,788 points, 39,148 candidates and 200 blank review pairs.
- Added a private, self-contained review page with readable desktop/mobile comparisons, expandable provenance, reviewer notes and CSV export. Original precision uncertainty remains visible.
- Added strict exported-label validation and aggregate-only receipts; detailed source/pair artifacts stay local and Git-ignored.
- Documented unsampled strata, unknown coordinate precision and evaluation limits. No matching model, tuning, automatic merges, canonical site IDs or later numbered steps have run. Step 6 awaits the spec's human-label gate.
