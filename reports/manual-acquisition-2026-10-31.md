# Browser acquisition report — October 31, 2026

**All seven core entries on the manual acquisition list are resolved.** One existing download was reused and nine files were downloaded through publisher browser controls. Ten original files (139,237,801 bytes) are now verified and organized in immutable source/date folders. The full local archive holds 16 sources, 49 files and 329,863,146 bytes.

Original files remain in Downloads. Copies are stored under `data/raw/<source_id>/2026-10-31/` with a `manifest.json` in each folder. Raw binaries remain Git-ignored; only code, source decisions and reports are pushed.

## File inventory

| Source | Original file | Bytes |
| --- | --- | ---: |
| ga_critical_2025 | [150802_01_0.xlsx](../data/raw/ga_critical_2025/2026-10-31/150802_01_0.xlsx) | 114,298 |
| ga_global_ree | [148953_00_0.pdf](../data/raw/ga_global_ree/2026-10-31/148953_00_0.pdf) | 834,061 |
| ga_global_ree | [148953_01_0.xlsx](../data/raw/ga_global_ree/2026-10-31/148953_01_0.xlsx) | 429,276 |
| usgs_global_critical | [PP1802_CritMin_FGDC_Metadata.xml](../data/raw/usgs_global_critical/2026-10-31/PP1802_CritMin_FGDC_Metadata.xml) | 23,435 |
| usgs_global_critical | [PP1802_Global_CriticalMinerals_gdb.zip](../data/raw/usgs_global_critical/2026-10-31/PP1802_Global_CriticalMinerals_gdb.zip) | 6,503,799 |
| usgs_global_ree | [USGS_Global_REE.gdb.zip](../data/raw/usgs_global_ree/2026-10-31/USGS_Global_REE.gdb.zip) | 95,388,954 |
| usgs_mcs_2026 | [MCS2026_Commodities_Data.csv](../data/raw/usgs_mcs_2026/2026-10-31/MCS2026_Commodities_Data.csv) | 3,189,010 |
| usgs_mrds | [rdbms-tab.zip](../data/raw/usgs_mrds/2026-10-31/rdbms-tab.zip) | 26,183,686 |
| wapha | [world_active_mines_17.05.2023.xlsx](../data/raw/wapha/2026-10-31/world_active_mines_17.05.2023.xlsx) | 818,717 |
| wapha | [world_inactive_mines_17.05.2023.xlsx](../data/raw/wapha/2026-10-31/world_inactive_mines_17.05.2023.xlsx) | 5,752,565 |

[Complete file inventory with SHA-256 hashes and original publisher URLs](manual-acquisition-files.csv). [All-source inventory](acquisition-inventory.csv).

## Verification and provenance

- Each copied file matches its original bytes. Every snapshot passes schema, complete-file coverage, size and SHA-256 verification; a second import returns `unchanged` and leaves files and manifests intact.
- ZIP archives and XLSX containers pass member CRC checks. The USGS REE archive contains located, nonspatial and reference table identifiers; the global critical-minerals archive contains point and polygon layer identifiers.
- MCS 2026 matches the publisher MD5 and byte count. Both WAPHA workbooks match their publisher SHA-256 hashes and byte counts. No publisher digest was available for the other files, so no independent publisher-hash match is claimed for those.
- MRDS is the original USGS compact relational ZIP, with MRDS, Commodity, Names, Materials, Dups, Accession, Place, About, schema.ini and mrds.met. It replaces the need to harvest the ArcGIS service for this fallback source.
- The GA 2025 workbook explicitly grants CC BY 4.0 in About!A19, with Coat of Arms/otherwise-noted exceptions. Its source registry now reflects that grant and the Commonwealth of Australia (Geoscience Australia) 2026 attribution.
- Acquisition methods distinguish the existing user download from browser downloads. macOS origin metadata confirms the existing REE archive’s ScienceBase file URL and record. Exact download timestamps were not available from browser/download metadata; `retrieved_at` remains null rather than using filesystem modification time. Separate `registered_at` values record archive registration in UTC. The date folder represents registration for these imported files.
- Browser redirects may contain temporary signed URLs; those tokens are not retained in the inventory or raw manifests. The stable public download URLs are retained.

## Scope

This completes the requested manual-file acquisition and organization. Normalized rows and published observation rows remain zero; parsing and row reconciliation belong to later numbered steps. No rows have been rejected or silently discarded during normalization because normalization has not begun. Individual government announcements remain a per-document country-context collection; Mindat remains excluded.

The programmatic HTTP downloader retains its contact User-Agent and robots/rate-limit checks. Browser downloads were carried out at Raj’s explicit request using visible publisher download controls. No login, CAPTCHA interaction or publisher outreach was required. Dryad initially delayed its active-mine download; both files subsequently arrived and passed the published digests.

Code validation: 119 Python tests, Ruff lint/format, 3 frontend manifest tests, Astro checks and the static build pass. The follow-up CI result is tracked with the pushed commit. No public site deployment occurred.
