# Manual acquisition queue — Phase 0 Step 2

**Core acquisition complete, October 31, 2026 (America/Los_Angeles).** Raj requested computer-use downloads and a check of Downloads. One existing archive was reused; the nine other required files were downloaded using the publishers’ visible browser controls. Verified original copies are organized under `data/raw/<source_id>/2026-10-31/`; originals in Downloads were preserved.

| Source | Original files | Outcome |
| --- | --- | --- |
| USGS global REE | `USGS_Global_REE.gdb.zip` | Reused existing download; macOS origin metadata matches ScienceBase. Located, nonspatial and reference table identifiers present; ZIP CRC passes. |
| USGS global critical minerals 2017 | `PP1802_Global_CriticalMinerals_gdb.zip`, `PP1802_CritMin_FGDC_Metadata.xml` | Browser downloads; point and polygon layer identifiers present; ZIP CRC passes. |
| USGS MCS 2026 | `MCS2026_Commodities_Data.csv` | Browser download; publisher MD5 and byte count match. |
| GA global REE | `148953_01_0.xlsx`, `148953_00_0.pdf` | Browser downloads; workbook and PDF formats verified. |
| GA 2025 workbook | `150802_01_0.xlsx` | Browser download; explicit CC BY 4.0 grant verified in `About!A19`, resolving catalogue uncertainty. |
| USGS MRDS fallback | `rdbms-tab.zip` | Publisher’s original compact relational export, including names, commodities, materials and `mrds.met`; ZIP CRC passes. |
| WAPHA | `world_active_mines_17.05.2023.xlsx`, `world_inactive_mines_17.05.2023.xlsx` | Browser downloads; both publisher SHA-256 hashes and byte counts match. |

The [browser acquisition report](../reports/manual-acquisition-2026-10-31.md) lists file checksums and original URLs. Every imported snapshot has complete file coverage, checksums and a separate registration timestamp. Exact original download timestamps are unknown where the browser did not expose them; file modification time is not substituted.

The programmatic downloader continues to obey host robots rules. Browser acquisition at Raj’s request is recorded separately. MRDS was obtained through its explicitly offered archive, resolving the need for bulk ArcGIS acquisition. Dryad initially delayed the active-mine download at its validation page, then both spreadsheets arrived without login or CAPTCHA interaction.

Individual government export announcements remain a per-document collection for the country-context step, not a missing bulk download. Each future row needs its dated official announcement and checked reuse terms. Mindat remains excluded under the spec.
