# Phase 0 · Step 4 source normalization

Run date: October 31, 2026, America/Los_Angeles.

**Status: local acceptance passed; remote CI gate required before Step 5.** Step 3 passed [CI for commit 8f0d02e](https://github.com/raj-clearviewsp/critical-minerals-map/actions/runs/41232185905) before this step began. Pilot filtering, site linkage and maturity assignment are not part of this commit.

## Outputs

Fifteen source-specific Parquet files under `data/staging/` implement a Pandera-validated `source_record` staging contract. The [normalization receipt](normalization-audit.json) binds the parser implementation, registry, vocabulary audit, raw manifest hashes, output hashes, table counts and rejection reasons. The [independent release counts](../sources/normalization/expected_counts.yaml) provide an additional guard against omitted rows. The official critical-minerals list remains a vocabulary authority rather than an observation dataset.

Each record retains its source-native identifier or explicitly derived row identity, source file/table/row, complete original fields, raw commodity/status/geology/size fields, references, available geometry, source URL, raw checksum, source currency and evidence grade. Manual downloads with unknown exact retrieval times retain null `retrieved_at`; their known archive registration time stays separate. JSON source cells preserve punctuation, qualifiers, repeated worksheet columns and linked child-table evidence.

Native IDs are scoped by source and table where needed. Sources without publisher IDs use source-content hashes and row positions or exact row content, preventing a reordered replacement workbook from assigning an old ID to a different record. No canonical ULIDs are minted here. Duplicate native identities and duplicate physical source rows are independently rejected by the writer.

## Row reconciliation

| Source | Staged records | Rejected rows | Located / unlocated |
| --- | ---: | ---: | ---: |
| `aiddata_transition_v2` | 4,996 | 0 | 0 / 4,996 |
| `ga_critical_2025` | 538 | 0 | 538 / 0 |
| `ga_critical_2025_arcgis` | 538 | 0 | 538 / 0 |
| `ga_global_ree` | 376 | 0 | 365 / 11 |
| `gseu_crm` | 842 | 1 | 842 / 0 |
| `icmm` | 7,104 | 0 | 7,103 / 1 |
| `jasansky` | 2,413 | 0 | 1,748 / 665 |
| `oecd_export` | 1,182,793 | 0 | 0 / 1,182,793 |
| `usgs_global_critical` | 2,125 | 0 | 2,125 / 0 |
| `usgs_global_ree` | 3,934 | 0 | 3,114 / 820 |
| `usgs_laterites` | 120 | 0 | 120 / 0 |
| `usgs_mcs_2026` | 8,886 | 0 | 0 / 8,886 |
| `usgs_mrds` | 304,388 | 0 | 304,388 / 0 |
| `usgs_us_critical` | 1,271 | 0 | 1,271 / 0 |
| `wapha` | 171,473 | 0 | 171,473 / 0 |
| **Total** | **1,691,797** | **1** | — |

Counts describe source observations, which include duplicated coverage, subsites, organizational groupings, financial relationships and country/policy data. They are not a count of unique mines. Every primary table must reconcile input rows to accepted rows plus explicit rejects. Reference/support tables have their own counts and roles and do not create extra deposits.

## Preserved distinctions and source issues

- USGS REE retains 3,114 located and 820 nonspatial records. The latter have null coordinates and `location_precision = none`. The repeated source `ID_No = 3352` remains in both original records; unique native geodatabase object IDs distinguish them.
- The 2017 global USGS compilation retains all 2,121 point and four polygon records, with source geometry preserved. No polygon centroid is calculated. The four polygon rows retain their publisher-supplied coordinate columns; the table’s located count means a usable source coordinate pair, not approval to plot a representative point.
- USGS U.S. rankings, GSEU size classes, GA operating/development/resource status, ICMM facility types and Jasansky facility/subsite relationships remain raw evidence. They do not yet assign maturity.
- GSEU has 842 records and one explanatory footnote row. The footnote is the sole primary-row reject, with its text and reason retained in the receipt.
- The GA workbook and independently captured API each contribute 538 records. Their overlapping coverage is retained for linkage in Step 6. GA global REE has 376 records, including 11 without coordinates; its `Element` field is tectonic geography. The explicitly documented REE scope is recorded separately.
- ICMM has 7,104 source facilities. One latitude contains a malformed minus-sign encoding (`ICMM07721`); its original value is retained and its usable coordinate pair is null with a review reason. No guessed coordinate correction is applied.
- Jasansky retains 2,413 source records: 2,390 facility/subsite records and 23 company/region `source_group` records. Its production, ownership and other linked tables remain evidence, without turning every production row into a new mine. Empty or nonsingleton geometries remain off point maps; no centroid is invented.
- MRDS retains all 304,388 main records, including 3,005 explicitly typed plants. Unmatched child rows are accounted separately: 287 names, 253 commodity rows, 16 material rows and 244 places. The larger Place table is a source relationship discrepancy, not parser loss. All original child tables remain in the immutable archive, and matched child rows retain source row references.
- WAPHA retains 21,164 active and 150,309 inactive worksheet rows. Country is unknown where absent from the source; activity labels remain dated source statements.
- OECD retains all 1,182,793 observations across 2009–2024, including blank/no-measure observations and all original dimensions. MCS retains all 8,886 rows before selecting the production/reserve measures needed in Step 8. AidData retains its distinct contributions, loan events, organization roles and ownership relationships, including exact duplicate source rows. Finance milestones never become mine operating status.

Coordinate values are validated for numeric type and range. Invalid, incomplete or zero/zero pairs remain in original evidence and receive a coordinate issue; they are not silently dropped. A populated point with undocumented positional accuracy has null staging precision. No under-5-km accuracy is inferred from decimal places. Precision review and canonical map eligibility remain later work; no staging record is plotted by this step.

## Verification and limits

- All **210 Python tests pass**; Ruff lint and formatting pass. Fixtures cover raw field preservation, code/finance separation, missing headers, dataset identities, duplicate rows, source-content identity changes, rejection accounting and commit rollback.
- The full run passes Pandera/Arrow validation and all fifteen independent release counts: **1,691,797 accepted records and one explained reject**. Output Parquet files total **188,480,649 bytes**.
- `make normalize-check` verifies every current source manifest, raw checksum, output checksum, Parquet schema and recorded row count. The standalone offline receipt check also passes.
- A complete second parse/write run reproduces all fifteen Parquet files and the receipt byte-for-byte, preserving their modification times. All 66 local raw files/manifest/marker paths remain byte- and time-identical.
- The frontend is unchanged from the verified Step 3 redesign. The corresponding pushed commit’s [GitHub Actions workflow](https://github.com/raj-clearviewsp/critical-minerals-map/actions/workflows/ci.yml) must pass before pilot filtering starts; it includes the site build.
- Canonical records, published observations and deployed pages remain **zero**.

The writer streams batches, validates them with Pandera/Arrow, and checks native and physical row uniqueness through temporary SQLite indexes. It stages all sources before accepting outputs. Conflicting existing artifacts are preserved, unchanged reruns retain bytes and modification times, and ordinary final-write failures roll back only newly created files. The receipt is written last and is the commit marker; files from an interrupted process without a valid receipt are not accepted inputs.

`make normalize` runs the real parsers when raw snapshots exist. `make normalize-check` verifies the local raw and staging bytes against the receipt; `make normalize-receipt-check` validates committed definitions and reconciliations without downloading data. CI runs fixture tests and the retained receipt check; the unavailable raw files and Parquet bytes are fully checked locally, not claimed to be rebuilt in CI. A fresh checkout can still exercise the original empty-input scaffold. Populated canonical production stages remain gated.

The original zero-row staging scaffold file is retained as historical generated output; the normalization receipt names the fifteen active source files explicitly. Canonical and published tables remain empty. No data has been deployed, and source redistribution restrictions remain in effect for the later publication gate.
