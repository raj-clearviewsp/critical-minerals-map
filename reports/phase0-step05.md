# Phase 0 · Step 5 pilot-mineral filter

Run date: October 31, 2026, America/Los_Angeles.

**Status: local acceptance passed; the pushed commit’s remote CI gate is required before Step 6.** Step 4 commit `1776a83` passed [CI run 41233471095](https://github.com/raj-clearviewsp/critical-minerals-map/actions/runs/41233471095) before filtering began.

## Result and outputs

The filter retains **80,451 of 1,691,797 source records** and explicitly excludes **1,611,346** without qualifying pilot evidence. Counts reconcile separately for every source. Original normalized rows are unchanged; the selected Parquet files append commodity and country evidence under `data/staging/pilot/`.

- [Source counts](pilot-counts-by-source.csv): input, retained, excluded and review flags.
- [Commodity and family counts](pilot-counts-by-commodity.csv): individual reported codes and the REE/Ni/Co families, kept separate.
- [Country counts](pilot-counts-by-country.csv): exact ISO host/activity countries; blank ISO means unassigned.
- [Record-kind counts](pilot-counts-by-kind.csv): deposits, facilities, groups, finance and country/policy observations.
- [Audit receipt](pilot-audit.json): input/output checksums, source reconciliation, code hashes, reports and review artifacts.
- [Unmapped terminology](../review/pilot-unmapped.csv) and [five source-scope review records](../review/pilot-scope-review.csv).

These are source-record counts before deduplication. A record can have several commodities, and the same physical asset can appear in several sources. Family and country categories can overlap; they must not be added together as unique mine counts.

| Source | Input records | Pilot records | REE family | Nickel | Cobalt |
| --- | ---: | ---: | ---: | ---: | ---: |
| `aiddata_transition_v2` | 4,996 | 1,291 | 71 | 924 | 791 |
| `ga_critical_2025` | 538 | 246 | 77 | 136 | 180 |
| `ga_critical_2025_arcgis` | 538 | 246 | 77 | 136 | 180 |
| `ga_global_ree` | 376 | 376 | 376 | 0 | 0 |
| `gseu_crm` | 842 | 138 | 19 | 58 | 61 |
| `icmm` | 7,104 | 305 | 21 | 214 | 188 |
| `jasansky` | 2,413 | 103 | 2 | 94 | 32 |
| `oecd_export` | 1,182,793 | 66,693 | 4,282 | 40,076 | 22,335 |
| `usgs_global_critical` | 2,125 | 264 | 52 | 0 | 212 |
| `usgs_global_ree` | 3,934 | 3,929 | 3,929 | 41 | 37 |
| `usgs_laterites` | 120 | 120 | 0 | 120 | 67 |
| `usgs_mcs_2026` | 8,886 | 515 | 286 | 127 | 102 |
| `usgs_mrds` | 304,388 | 4,701 | 1,400 | 2,798 | 1,619 |
| `usgs_us_critical` | 1,271 | 142 | 76 | 51 | 51 |
| `wapha` | 171,473 | 1,382 | 34 | 846 | 739 |
| **Total source records** | **1,691,797** | **80,451** | **10,702** | **45,621** | **26,594** |

The retained record kinds are 9,894 deposit records, 2,056 facility records, two source groupings, 1,291 financing/relationship records, 515 country-commodity observations and 66,693 export-policy observations. The latter include source no-measure and blank observations and are not a count of active restrictions. MCS rows still include measures beyond the production/reserve subset needed in Step 8. The fifteen pilot Parquet files total 13,942,019 bytes.

## Selection rules and preserved meaning

A source-supported non-collateral REE, nickel or cobalt association qualifies a record. Explicit primary, secondary, tertiary and byproduct roles remain distinct. When a geological source does not rank commodities, its reported association remains unspecified; no economic primary/byproduct role or current production is invented. A source-scope fallback is allowed only where documented and narrowly defined.

- The REE family includes reported aggregate REE and individual rare-earth elements. An aggregate never expands to invented individual element quantities. OECD HS mappings describe product/policy scope, not deposits or production of every possible group component.
- MRDS linked Commodity rows recover 1,743 qualifying records that its main `code_list` alone misses. Each retained child claim has a pointer back to the raw record. Repeated primary/secondary/tertiary claims remain distinct; exact duplicates do not inflate record counts.
- Laterites retain all 120 nickel records, including three using the documented nickel-dataset scope when no usable nickel grade cell is available. Only 67 have positive source cobalt-grade evidence. Missing, zero or upper-bound-only cobalt grades do not assert cobalt presence. Seven complex nickel cutoff cells on four already-retained records remain quantitatively unparsed and visible in review; no grade or quantity conversion is performed.
- The GA global compilation’s documented REE scope qualifies all 376 records. Its tectonic `Element` field is never treated as a commodity.
- Qualifiers remain raw and flagged. **526 retained records** have qualified pilot evidence: 226 in USGS REE and 150 in each GA 2025 variant. A `+/-` or question mark does not become a confirmed byproduct claim.
- AidData’s `Commodity` collateral field does not qualify financed mineral activity. Only the explicit transition/primary/subsidiary mineral fields do. Organization-role rows remain financial relationships, and the 306 ownership rows with no commodity association do not receive minerals inferred from names. Jasansky waste-only material content does not qualify a record; original waste rows remain intact in normalization.

## Country assignments and review

Country codes describe the asset/project host or activity geography. Lender, recipient-incorporation, organization-origin and shareholder-origin countries remain separately labelled evidence and never populate host-country counts. Exact ISO fields are preferred; conflicting valid fields or invalid explicit ISO codes prevent assignment. Only explicit multi-country host lists can generate multiple assignments.

The documented USGS U.S. state jurisdiction resolves U.S. states/DC to USA and Puerto Rico to PRI. Australian source-state fields resolve to AUS. No country is inferred from coordinates, names of companies, or unrecognized FIPS values. **6,141 retained records have no assigned ISO host country**, predominantly MRDS’s FIPS-only geography and WAPHA’s absent country field; world/regional aggregates also remain unassigned. No fabricated ISO code is written into the Parquet.

The terminology queue contains **3,818 distinct unresolved source-field entries**, representing 1,491,462 record-term occurrences across input records. Most are outside the pilot vocabulary; some are complex unparsed quantitative text. Mixed cells keep their successfully mapped pilot association alongside unresolved pieces.

Five records in the global REE compilation explicitly list nonpilot commodities: Coola, Tchivira, El Muerto, Beldih and Khairidih. Four also have REE-bearing mineralogy text. The filter does not override their nonblank commodity cells from the dataset title or unreviewed mineralogy. Their full identifiers, source references, commodity/mineralogy text and empty decision fields are exported for review. Any approved correction belongs in versioned `overrides/`, with a deliberate rerun and updated counts.

## Validation and next gate

- **303 Python tests pass**, including source-shaped cases and writer/receipt tampering checks. Ruff lint and formatting pass.
- Full local `pilot-check` passes: all source totals reconcile, every retained row has eligible pilot evidence, host-country evidence and record counts agree, and input/output hashes and Parquet schemas validate.
- A full second filtering run reproduces all **22 pilot/report/review artifacts** byte-for-byte without changing modification times. All **16 parent normalization artifacts** and **66 raw file/manifest/marker paths** remain unchanged.
- Independent direct-field scans agree with retained counts for all fifteen sources. Tests prevent collateral-only and waste-only qualification, country assignment from financiers, inconsistent host-code fallback, changed inherited fields and malformed helper evidence.
- Code, report and changelog are ready for the corresponding pushed commit’s [GitHub Actions gate](https://github.com/raj-clearviewsp/critical-minerals-map/actions/workflows/ci.yml). The unchanged site build is also checked by that workflow before the next step.

The writer reads bounded batches, validates inherited and appended schemas, records every exclusion reason, and stages outputs before receipt-last acceptance. Source fields, native IDs, coordinates, dates and raw evidence are preserved exactly. Local checks verify raw sources, parent staging files, pilot bytes and category counts; offline CI replays reconciliations and regenerates count CSVs from the retained receipt. It does not claim to rebuild unavailable raw datasets.

Step 6 begins only after this commit’s CI passes. Its separate hand-labelled 200-pair gold set must precede linkage tuning and any auto-merge precision claim. No canonical sites, merged entities, maturity assignments or published observations exist at this checkpoint. The cleaner site preview remains unchanged; no public deployment occurred.
