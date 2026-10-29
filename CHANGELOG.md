# Changelog

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
