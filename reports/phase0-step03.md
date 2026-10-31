# Phase 0 · Step 3 vocabulary review

Run date: October 31, 2026, America/Los_Angeles.

**Status: complete.** Commit `8f0d02e` passed [GitHub CI run 41232185905](https://github.com/raj-clearviewsp/critical-minerals-map/actions/runs/41232185905). The vocabulary definitions, source-format readers and retained audit validate. Step 4 began only after this remote gate passed.

## Scope and outputs

This step reviews terminology in the verified source archive. It defines commodity, deposit-type and status aliases, ISO country codes and explicit source-specific overrides. It preserves the source field and raw value for every observed term. Reading source tables for this audit does not create normalized `source_record` rows or canonical sites, assign maturity, deduplicate deposits or convert quantities.

- [Vocabulary definitions](../sources/vocab/): controlled terms, source-specific aliases, field rules and country overrides.
- [Audit receipt](vocabulary-audit.json): input and vocabulary hashes, source coverage and pilot coverage result.
- [Observed terminology](vocabulary-observations.csv): source, file, table, field, raw value, count, resolution and unresolved terms.
- [Source-field evidence](vocabulary-source-fields.json): inspected tables, code labels and source-format notes.
- [Unmapped review queue](../review/unmapped-vocabulary.csv): unresolved observed terms retained for review.

## Semantic decisions

- The retained official USGS 2025 list is the authority for individually named `on_us_list` entries. Grouped REE/PGE labels are not silently treated as individual list entries.
- Commodity splitting is field-specific. MRDS atomic commodity names such as `Nickel, Refiner` retain their commas, while its documented code list uses spaces. Source-qualified aliases take precedence over global aliases. The MRDS `RE` code means rhenium, not rare earths.
- Oxide and grouped commodity aliases identify terminology only. They do not convert quantities, add grouped and individual measurements together, establish production or infer a byproduct from a missing grade.
- Raw uncertainty marks remain evidence. Resolving an uncertain term does not make the assignment certain. Ambiguous country labels remain unresolved; historical names and spelling variants require explained exact overrides. Non-ISO source codes are not passed off as ISO codes.
- Deposit-type mappings are conservative. Explicit carbonatite, laterite, placer and other reviewed types can map to broad categories; incompatible composite geology remains unmapped. Generic clay and lithium clay do not imply ion adsorption. The USGS nickel-cobalt laterite subtype categories map only within that source to the broad laterite type.
- Historical `Producer` labels map to `producer_reported`; WAPHA and Jasansky `Active` labels map to `active_reported`. Neither is evidence of current operation. Explicit operating labels still describe the source's reference date and do not prove that the particular critical mineral is produced.
- USGS REE occurrence/showing/deposit labels and production status are separate source fields. USGS U.S. resource and producer categories retain their raw qualifiers. Vocabulary resolution assigns no maturity rung.
- MRDS `Plant` is a facility kind, Jasansky `Project` lacks a clear activity stage, and AidData finance/project milestones do not establish mine status. These remain unmapped. Incompatible historical/activity composites remain visible for review.
- Source-format handling retains nonspatial USGS REE evidence, the Jasansky facility/subsite distinction, raw GA status and source-specific coded geography. The laterite CSV's encoding and embedded line breaks are handled without discarding raw labels.

## Coverage checkpoint

The gate requires zero unmapped pilot commodity associations across nickel, cobalt and REE terminology. It does not require ambiguous nonpilot geology, finance milestones or geographic labels to be forced into a category. Counts of distinct source-field values and observed cells are terminology-audit measures, not counts of unique deposits.

| Measure | Final result |
| --- | --- |
| Verified source snapshots and input files | 16 snapshots; 49 manifested input files |
| Inspected source tables | 66 tables |
| Distinct source-field values and observed cells | 23,790 distinct field values; 4,747,179 observed cells |
| Pilot commodity field values | 2,640 |
| Pilot commodity associations still unmapped | 0 |
| Other unmapped source-field values | 14,947, listed in the review CSV |
| Official USGS list membership check | All 60 individually named official minerals agree |
| Normalized observation rows | **0** |
| Published observation rows | **0** |

## Local commands and CI

```sh
make vocab        # verifies local originals and rebuilds the terminology audit
make vocab-check  # verifies the retained audit without local raw files or network
make check        # Python lint, formatting and tests
make site         # frontend checks and static build
make preview      # local preview on 127.0.0.1:4321
```

The full audit requires the verified raw archive. The CI check validates vocabulary and observation hashes and replays term resolution from the retained audit; it does not download sources or reverify unavailable raw bytes. GitHub Actions runs this check after Python tests and before the existing data and site jobs. `make vocab` and `make vocab-check` are separate from the empty data pipeline, which continues to refuse populated inputs until normalization is implemented in its numbered step.

| Verification | Final result |
| --- | --- |
| Python lint, formatting and tests | 163 tests pass; Ruff lint and format checks pass |
| Full source audit and retained-audit check | Both pass; 589 exact code expectations pass |
| Unchanged audit rerun / deterministic artifacts | All four audit artifacts preserve SHA-256 and modification times |
| Raw archive unchanged by vocabulary audit | All 66 local raw files, including manifests/auxiliary files, preserve bytes and modification times |
| Frontend tests, Astro diagnostics and build | 3 tests pass; zero Astro errors/warnings/hints; static build passes |
| Browser review, responsive layout and links | Browser checked at 390, 641 and 1280 px; no horizontal overflow; anchor navigation works |
| Step 3 commit and GitHub Actions run | Commit `8f0d02e`; [CI run 41232185905](https://github.com/raj-clearviewsp/critical-minerals-map/actions/runs/41232185905) passed |

## Preview redesign

The site remains an honest empty-data preview. Its revised layout uses spacious readable type, a restrained palette, ordinary navigation and real section anchors. Decorative numbering, small status badges and implementation-focused copy were removed. The page clearly says no records have been published and offers no invented map, fake data or premature downloads. Styles remain responsive and printable; no client JavaScript, analytics, external fonts or remote runtime assets were added.

The Make preview target now calls the existing npm preview script directly, avoiding a duplicate `--host` argument. Browser review and final build verification are recorded above. No website has been deployed.

## Unmapped terms and limits

The review CSV lists every unresolved whole source-field value and its unmatched components: 11,396 commodity values, 3,188 geographic values, 348 deposit-type values and 15 status values. These include nonpilot minerals and trade products, non-ISO MRDS/laterite codes, unresolved historical geography, composite geology and ambiguous activity labels. A mixed cell can contain a successfully mapped pilot mineral and another unresolved term. No records are rejected in this terminology step; future parsers must retain the raw evidence.

There are 89 commodity entries, 23 deposit types, 19 statuses, 249 ISO countries and 100 explained source-country aliases. The REE family follows the current [USGS NMIC 17-element definition](https://www.usgs.gov/centers/national-minerals-information-center/rare-earths-statistics-and-information), including scandium; aggregate labels never imply that every element is present. Promethium is tracked as an REE but is not independently named on the 60-mineral critical list.

Coverage checks use independent publisher code labels for 27 OECD product codes and 47 Jasansky dictionary codes, plus exact source cases and lexical guards. Negative controls prevent exclusion-only HS descriptions, carbon dioxide, lithium carbonate and MRDS soda-ash codes from acquiring pilot associations. Tests remove a correct cobalt mapping and add an incorrect nickel mapping to verify both errors fail. Raw snapshot selection is date-bound and requires the complete reviewed source inventory. The offline receipt binds the vocabulary, readers, schema, dependency lock, observations, field evidence and review queue. It cannot establish freshness of raw files unavailable to CI.
