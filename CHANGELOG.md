# Changelog

## 2026.10.31 — Phase 0, Step 3 vocabulary review (gate pending)

- Added source-aware commodity, deposit-type and status vocabularies, ISO country definitions and explained country overrides.
- Added source-field terminology audits, retained source-format evidence, an unmapped-term queue and a reproducible coverage receipt. Pilot coverage and final validation results are pending in the Step 3 report.
- Kept historical production, active labels, uncertain classifications, facility kinds and finance milestones distinct; vocabulary matching does not assign maturity or current operating status.
- Added `make vocab` for the full local-source audit and `make vocab-check` for retained-audit validation in CI without raw downloads.
- Refreshed the empty Astro preview with readable typography, straightforward navigation and a clear unpublished-data message; removed decorative numbering and implementation-oriented copy.
- Fixed the preview Make target to use the host already configured by the npm script.
- Step 3 remains open until its acceptance checks, final report, pushed commit and remote CI pass. Step 4 normalization has not started; no data or site has been deployed.

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
