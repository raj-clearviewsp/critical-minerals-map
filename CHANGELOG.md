# Changelog

## 2026.11.04 — Accept researched decisions and prepare the next 60

- Recorded Raj's explicit chat approval of all 60 final recommendations as a new immutable review round, preserving the exact statement, sample and research checksums, and approved labels.
- Preserved all 47 earlier events; the new history contains 107 events across 94 comparisons. The 26 newly approved unresolved outcomes remain unresolved alongside seven earlier cases.
- Prepared the remaining 27 development comparisons with primary-source research and separate agent review, plus the first 33 blind holdout comparisons, as Raj requested. The other 47 holdout cases remain unpresented.
- Kept research and blind source evidence in separate pages, payloads, storage and exports, with an overview linking both sections. Added immutable acceptance, sample-bound export validation and aggregate CI checks.
- Final development proposals: five same-site, nine distinct-site, one containment and twelve unresolved. All 27 have a separate second review; six relationship proposals changed after challenge and cross-case calibration. The dossier has 110 citation entries with access and independence notes.
- Validation: 814 tests pass; both local rebuild and offline receipt checks pass. All 74 prior accepted artifacts retain identical checksums and modification times. Browser checks cover blank decisions, isolated progress, CSV quoting and newlines, navigation, and desktop/narrow layouts.
- No source data, human decisions or detailed research is committed publicly; no model fitting, automatic merging or deployment occurs.

## 2026.10.31 — Researched development review, first 60 cases

- Prepared the first 60 of the frozen 87 new development comparisons for detailed research-assisted approval, preserving exact source identities and order.
- Added per-case research interpretations, geographic/scope/geology analysis, alternative explanations, uncertainty, cited evidence with access and independence notes, and a separate second-review record.
- Added a self-contained approval workspace and a complete searchable/printable dossier. Recommendations never initialize human labels; explicit approval affects only the current case, and exports remain bound to the exact packet.
- Added immutable acceptance, local reproducibility checks, offline aggregate receipt checks and tests for preservation, approval separation and output safety.
- Kept all earlier decisions, the other 27 development cases and the 80-case holdout unchanged. No new human labels, model fitting, automatic merges or public observation data.
- Final proposals: 12 same-site, 17 distinct-site, 5 containment and 26 unresolved. Independent review changed five relationship recommendations and raised one containment confidence; all 60 have a second-review record.
- Validation: 785 tests pass, local reproducibility and offline receipt checks pass, and browser checks cover the complete dossier, blank decisions, navigation and responsive layout. All 64 previously accepted artifacts retain identical checksums and modification times.

## 2026.10.31 — Private source-record website and completed adjudication

- Preserved the latest seven adjudications exactly, alongside all 40 previous review events. The latest view contains 34 comparisons; seven relationships remain unresolved.
- Added a reproducible private dataset with 10,788 distinct source observations from 12 sources. No records were merged and no maturity or ownership claims were inferred.
- Built an interactive map and accessible results list with search, mineral/country/source/scope filters, record details, source citations, URL state and filtered CSV export.
- Added locally bundled MapLibre and a pinned Natural Earth basemap, responsive editorial styling, Sources and About pages, and a double-click local launcher.
- Kept private data and private builds Git-ignored. Standard builds explicitly contain zero observations; CI verifies the aggregate adjudication receipt and builds only the empty site.
- Site development proceeds alongside validation at Raj's request. The formal 200-comparison validation still needs 167 new comparisons and resolution of seven earlier relationships before model tuning; this does not block use of the private source-record explorer.

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

## 2026.10.31 — Step 6 source-aware triage and focused pilot

- Preserved the user's original CSV and all twenty decisions, reviewer names and notes without changing their meanings.
- Added evidence-backed record scope, subsite names, contextual versus identity aliases, placeholder handling, geological compatibility and coordinate-reuse checks.
- Routed all 39,148 original candidates into explained queues; 17,718 are lower-priority likely-distinct audits. No candidate was silently deleted or merged.
- Verified 246 GA publisher-view correspondences and consolidated 985 redundant comparisons for reviewer workload while retaining both provenance trails.
- Added a 20-case local pilot with geographic reference maps, neighboring records, direct source evidence, preserved prior answers and explicit containment/uncertainty choices.
- Added separate pilot-export validation, reproducible local artifacts and aggregate-only CI receipts. Original linkage, raw and staging artifacts remain unchanged.
- Validation: 581 Python tests pass; browser checks cover responsive layout, read-only prior answers, separate CSV export and saved progress using synthetic cases.

### Step 6 — completed pilot reconciliation and validation preparation

- Preserve both review rounds as 40 immutable events and 34 unique pairs, with a separate latest-answer view.
- Add 30 cited source findings across 13 cases: 7 adjudications, 5 open research questions and 1 corroborated relationship.
- Freeze 200 comparison units with a 120 development / 80 holdout split, retaining 33 earlier comparisons and excluding 1 direct publisher-view duplicate. Isolate connected candidate neighborhoods between splits.
- Add local evidence-review and blinded validation pages, separate sample-bound CSV exports, deterministic receipts and CI checks. Human answers and detailed sources stay local; no models or merges run.
- Validation: 737 tests pass; desktop/mobile browser checks confirm source citations, case navigation, read-only history, blank new decisions and a blinded holdout payload.
