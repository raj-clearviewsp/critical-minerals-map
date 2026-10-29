# Critical Minerals Atlas — Implementation Spec

Oct 28, 2026 · @Raj Sawhney

## How to use this spec

Build the Critical Minerals Atlas in three phases, strictly in order: Phase 0 (open-data deposit map), Phase 1 (filings-based project pipeline and ownership), Phase 2 (analysis layers). Each phase is broken into numbered steps; finish and verify one step before starting the next.

**Who this is for.** An implementing agent with a shell, Python 3.12+, Node 20+, git, and web access, working in a single repository. A human owner (Raj) reviews at the end of every phase and approves anything in the stop-and-ask list below.

**Working rules**

- Never invent data. Every value in a published table traces to a source record with URL, retrieval date and evidence grade (see Canonical data model).
- Raw downloads are immutable. Store them under `data/raw/<source_id>/<YYYY-MM-DD>/` with a checksum; all transformation happens in code.
- Every step ends with a commit, passing tests, and a short run report in `reports/` (what was ingested, row counts, rejects, open issues).
- Respect source terms: identify yourself with a contact User-Agent, obey rate limits, do not scrape sites whose terms prohibit it, and do not redistribute data whose license forbids it.
- Prefer boring, durable tools. No database servers, no login, no paid services in Phase 0.

**Definition of done for any step**

1. Output files exist at the paths named in the step and pass schema validation.
2. Acceptance checks listed in the step pass (automated where possible).
3. Run report written; row counts and rejects explained.
4. Changelog entry added; commit pushed.

**Stop and ask Raj before**

- Using any source whose license is unclear, non-commercial, or requires login or payment.
- Publishing any statement linking a named company or person to a foreign state that is not a direct equity percentage or named shareholder from a filing or official register.
- Changing the canonical schema after the end of Phase 0.
- Deploying anything to a public URL.

## Purpose, users and principles

The Atlas answers one question fast: where are the world's critical mineral deposits, how real is each one, and who owns it. It is a free, public, sourced reference for Senate Foreign Relations Committee staff and others who work on minerals policy.

**Primary user.** A legislative staffer with 10 minutes, a Senate network, and no GIS training. Recurring jobs:

| Job | Question the Atlas must answer |
| --- | --- |
| Country prep (CODELs, nominations, meetings) | What does this country have, at what stage, and who controls it? |
| Oversight | Which projects outside China are closest to production, and which have announced U.S. support? |
| Legislation support | How concentrated is supply of mineral X, and how much non-China capacity is in the pipeline? |
| Rapid response (e.g. a new Chinese export control) | If China restricts X, what supply remains, and which projects could fill the gap and when? |

**Deliberately out of scope.** Staff already get these, often faster, from agencies or CRS: agreement and MOU texts and their status; detailed DFC, EXIM and DoD transaction terms; embassy-style country briefings; general explainers. Link to public sources where useful, but do not build trackers for them.

**Product principles**

1. **Fast.** Static site, no login, first meaningful paint under 2 seconds on a slow connection.
2. **Sourced.** Every field shows its source, retrieval date and evidence grade on click.
3. **Honest about maturity.** Every point carries a resource-maturity rung, so occurrences are never mistaken for reserves.
4. **Neutral.** Report facts (equity percentages, named shareholders, stated financing) rather than labels such as "China-linked." No partisan framing.
5. **Portable.** Every page exports to PDF; every table downloads as CSV.

**Phase 0 success test.** A staffer can answer "Where are rare earth deposits outside China, how many have a code-compliant resource or better, and who operates the producing ones?" in under 60 seconds.

## Scope

Cover a short list of chokepoint minerals deeply rather than all 60 minerals on the [2025 U.S. critical minerals list](https://www.usgs.gov/news/science-snippet/interior-department-releases-final-2025-list-critical-minerals) thinly. Geography is global from day one; depth grows by phase.

**Minerals by phase**

| Phase | Minerals | Why |
| --- | --- | --- |
| 0 (pilot) | Rare earth elements (track total REO plus Nd, Pr, Dy, Tb where reported), nickel, cobalt | Highest-salience chokepoint; strong open deposit data (USGS global REE, USGS Ni-Co laterites); Southeast Asian laterites match the owner's expertise |
| 1 | Add graphite, lithium, gallium, germanium, antimony, tungsten | Recent Chinese export controls; mix of primary and byproduct supply |
| 2+ | Candidates: copper, PGMs, manganese, vanadium, niobium, tantalum | Only after Phase 2 analysis works end to end |

Gallium and germanium are mapped through their host deposits and plants (bauxite/alumina, zinc, coal fly ash), because they are rarely mined on their own.

**Layers**

| Layer | What a record is | Maturity rungs | Built in |
| --- | --- | --- | --- |
| Deposits | A known mineral occurrence or deposit, from geological surveys | 1 to 2 | Phase 0 |
| Facilities | An operating mine, smelter, refinery or separation plant | 5 | Phase 0 (mines), Phase 2 (processing) |
| Projects | A development-stage asset with a code-compliant resource or reserve, from company filings | 3 to 4 | Phase 1 |
| Country and mineral context | Production shares, reserves, export restrictions, Chinese official finance | n/a | Phase 0, deepened in Phase 2 |

**Explicitly excluded** (revisit only on Raj's instruction)

- Agreement, MOU and framework texts or status tracking.
- Transaction terms for DFC, EXIM, DoD or DOE deals. Announced U.S. support appears only as a tag on an asset, with a link to the public announcement.
- Fuel and fertilizer minerals on the U.S. list (metallurgical coal, uranium, potash, phosphate).
- Deep-sea nodules, recycling and mine-waste recovery. Note the EU is building a mine-waste CRM database; consider it later.
- Prices and market forecasts (licensed data; out of scope for a free public tool).

## Architecture and tech stack

A batch Python pipeline turns versioned raw downloads into canonical tables, then publishes static files (CSV, GeoParquet, PMTiles, JSON) that a static site reads directly. There is no application server or database to run in production.

&#91;embedded content: data pipeline · sources to site\]

Uncertain matches and extractions go to a human review queue; approved fixes are saved in `overrides/` and re-applied on every run.

**Stack**

| Concern | Choice | Notes |
| --- | --- | --- |
| Language and env | Python 3.12, `uv` for dependencies | Pin everything in `uv.lock` |
| Tabular and spatial processing | DuckDB with the `spatial` extension, GeoPandas, `pyogrio` | DuckDB for joins and exports; GeoPandas for geometry ops |
| Canonical storage | Parquet / GeoParquet files in `data/canonical/`, committed via Git LFS or a release asset | CSV mirrors for public download |
| Schema validation | `pandera` (DataFrame schemas) | One schema per canonical table; CI fails on violations |
| Record linkage | `splink` (probabilistic linkage) plus `rapidfuzz` for name similarity | Spatial blocking first, then name and commodity comparison |
| Document extraction (Phase 1) | `pdfplumber` / `pymupdf` for text and tables; an LLM with structured-output schemas for field extraction | Every extracted value keeps page number and quoted snippet |
| Vector tiles | `tippecanoe` to build tiles, output as a single `.pmtiles` file | Rebuilt on each data release |
| Frontend | Astro (static site generator), MapLibre GL JS, `pmtiles` protocol, Pagefind for static search | Islands of interactivity only where needed |
| Basemap | Self-hosted Protomaps basemap extract (OpenStreetMap data, ODbL attribution required) | Self-host fonts, glyphs and sprites; no third-party calls at runtime |
| Charts | Observable Plot | Small, static-friendly |
| PDF export | Print stylesheet per page; optional Playwright pre-render of country PDFs at build | No server-side rendering |
| CI/CD | GitHub Actions: test, build data, build site, deploy | Data build and site build are separate jobs |
| Hosting | Cloudflare Pages for the site; Cloudflare R2 for `.pmtiles` and bulk downloads | R2 supports HTTP range requests and has no egress fees; configure CORS to allow `Range` headers |

Why PMTiles: it is a single-file tile archive served by HTTP range requests, so no tile server is needed ([MapLibre docs](https://maplibre.org/flutter-maplibre-gl/advanced/pmtiles/), [Protomaps hosting guide](https://docs.protomaps.com/pmtiles/cloud-storage)). Government networks often block unfamiliar third-party domains, so the site must load fully from its own domain plus R2.

**Repository layout**

```
atlas/
  sources/            # one YAML per source: URL, license, cadence, parser
  pipeline/
    fetch/            # downloaders -> data/raw/<source>/<date>/
    normalize/        # per-source parsers -> data/staging/<source>.parquet
    harmonize/        # map staging -> canonical schema
    link/             # entity resolution -> site clusters
    enrich/           # country codes, maturity rung, ownership rollups
    qa/               # pandera schemas, checks, reports
    publish/          # CSV, GeoParquet, PMTiles, page JSON
  overrides/          # human-reviewed YAML corrections (versioned)
  review/             # review queue exports and decisions
  data/{raw,staging,canonical,publish}/
  site/               # Astro app
  reports/            # run reports per step
  tests/
```

Rule: nothing in `data/canonical/` is edited by hand. Human corrections live in `overrides/` and are applied by the pipeline, so every fix is reviewable and reproducible.

## Canonical data model

Every map point is a **site**: one physical deposit or mine, assembled from one or more source records. Everything else (resource estimates, facilities, ownership, production) hangs off a site, and every value carries its provenance.

**Tables** (Phase introduced in brackets)

| Table | One row is | Key fields |
| --- | --- | --- |
| `source` \[0\] | A dataset or document collection | `source_id`, name, publisher, url, license, `redistribution_ok` (bool), currency date, retrieved\_at, checksum |
| `source_record` \[0\] | One record as it appeared in a source | `record_id` (= `source_id` + native id), raw name, aliases, lat, lon, `location_precision`, country, raw commodities, raw deposit type, raw status, raw size/grade text, reference |
| `site` \[0\] | A canonical deposit or mine | `site_id`, name, alt\_names, point geometry, `location_precision_m`, `country_iso3`, admin1, primary and byproduct commodities, `deposit_type` (controlled vocabulary), `maturity_rung`, `reporting_system`, `status`, `review_state` |
| `site_member` \[0\] | Link from a source record to a site | `site_id`, `record_id`, `match_score`, `method` (auto / manual) |
| `facility` \[0\] | A mine, concentrator, smelter, refinery, separation or magnet plant | `facility_id`, `site_id` (nullable for stand-alone plants), `facility_type`, commodities, capacity and unit, status, operator name |
| `production` \[0\] | Output for one facility or country, commodity and year | entity ref, commodity, year, quantity, unit, source |
| `country_commodity` \[0\] | Country-level production and reserves | `country_iso3`, commodity, year, production, reserves, world share, source |
| `export_restriction` \[0\] | A restriction on one commodity by one country | country, commodity, measure type, start date, end date, source |
| `resource_estimate` \[1\] | One line of a resource or reserve statement | `site_id`, reporting code, category (measured, indicated, inferred, proven, probable, historical), tonnage (Mt), grade and unit, commodity, contained metal (t), cut-off, effective date, `doc_id`, page, snippet |
| `project` \[1\] | A development-stage asset as companies describe it | `project_id`, `site_id`, name, stage (exploration, PEA, PFS, FS, construction, commissioning), study dates, capex (as stated), target first production, `doc_id` |
| `entity` \[1\] | A company, government, fund or state body | `entity_id`, legal name, aliases, type, country of incorporation, listing tickers, LEI, SEC CIK |
| `ownership` \[1\] | An entity's stake in a site or project | asset ref, `entity_id`, `equity_pct`, role (owner, operator, offtaker), as-of date, `doc_id`, evidence grade |
| `entity_relation` \[1\] | A parent-subsidiary or shareholder link | parent, child, `equity_pct`, as-of date, `doc_id`, evidence grade |
| `us_support_tag` \[1\] | A public announcement of U.S. government support | asset ref, agency, instrument type, stated amount, date, stage (announced / authorized / disbursed), url |
| `china_official_finance` \[0\] | An AidData record linked to a country or site | AidData project id, country, amount, year, lender, link to `site_id` where known |
| `document` \[1\] | A filing or report used as evidence | `doc_id`, url, issuer, doc type, effective date, retrieved\_at, checksum |

**Resource maturity ladder**

Assign each site the highest rung its evidence supports. Store the evidence row that justifies the rung.

| Rung | Label | Rule |
| --- | --- | --- |
| 1 | Occurrence | Presence reported; no tonnage or grade |
| 2 | Historical or non-compliant estimate | Tonnage and grade reported, but not under a CRIRSCO-family code, or explicitly historical |
| 3 | Code-compliant resource | Current measured, indicated or inferred resource under JORC, NI 43-101, S-K 1300, SAMREC, PERC or another CRIRSCO code |
| 4 | Reserve | Current proven or probable reserve under one of those codes |
| 5 | Operating | Production reported within the last 24 months, or operating status from a dated source |

Two caveats must show in the UI. Status overlays (closed, care and maintenance, suspended) are stored separately from the rung. And large deposits in China, Russia and some other countries report under national classification systems rather than CRIRSCO codes; store `reporting_system` and show a "national system" badge so these are not read as small or speculative.

**Evidence grades** (on every ownership, estimate and support row)

| Grade | Meaning | Examples |
| --- | --- | --- |
| A | Primary official document | Securities filing, technical report, government register, company annual report |
| B | Official compilation | USGS, Geoscience Australia, GSEU |
| C | Reputable secondary | Peer-reviewed dataset, ICMM, AidData |
| D | Press or media report | Company press release on a newswire, news article |

**ID and vocabulary rules**

- `site_id`, `entity_id`, `project_id` are ULIDs, minted once and stored in `overrides/id_registry.parquet`. Never reuse or renumber; merges keep the older id and record the retired one as an alias.
- Countries use ISO 3166-1 alpha-3. Commodities use a controlled list in `sources/vocab/commodities.yaml` mapped from each source's codes.
- Deposit types use a controlled list (e.g. carbonatite, ion-adsorption clay, placer/heavy mineral sands, laterite, magmatic sulfide, pegmatite, brine) mapped from source terms; unmapped terms go to a review queue.
- Coordinates are WGS84. `location_precision` is one of exact, approximate (under 5 km), district, or country centroid; never plot centroid-only records as points.

## Source registry

Phase 0 runs entirely on open data. Sources marked "confirm" need a license check before any of their values are published; ingest them, but keep them out of public outputs until confirmed. Each source gets a YAML file in `sources/` (id, name, publisher, URL, license, redistribution\_ok flag, cadence, parser) before any code is written for it.

**Deposits (geology)**

| Source | Coverage | Currency | Reuse | Role |
| --- | --- | --- | --- | --- |
| [USGS Critical mineral deposits of the United States, ver. 3.0](https://www.usgs.gov/data/critical-mineral-deposits-united-states-ver-30-september-2026) (DOI 10.5066/P9K1HBNT) | [1,271 U.S. deposits, 41 states and Puerto Rico, 59 minerals](https://www.usgs.gov/news/national-news-release/usgs-updates-us-critical-mineral-deposits); classified by mineral system, deposit type, and resources/production | Sept 2026 | U.S. public domain | Primary U.S. layer. Its producer/resource ranking maps onto the maturity ladder |
| [USGS Global REE occurrence database](https://www.usgs.gov/data/global-rare-earth-element-occurrence-database) (DOI 10.5066/F7DR2TN4) | 3,100+ located records, 820 unlocated, 1,590 references; mineralogy, deposit type, size and grade where known | Compiled 2018 | CC0 | Primary global REE layer |
| [USGS Global distribution of mines, deposits and districts of critical minerals](https://catalog-beta.data.gov/dataset/global-distribution-of-selected-mines-deposits-and-districts-of-critical-minerals) | 22 minerals, global points and polygons | 2017 | U.S. public domain | Global layer for Ni, Co, graphite, Li, Ga, Ge, Sb, W |
| [USGS Ni-Co laterite deposits of the world](https://pubs.usgs.gov/of/2011/1058) (OFR 2011-1058) | 120 explored laterite deposits with grade and tonnage | 2011 | U.S. public domain | Ni/Co laterites (Indonesia, Philippines, New Caledonia) |
| [USGS MRDS](https://services.arcgis.com/v01gqwM5QqNysAAi/ArcGIS/rest/services/Mineral_Resources_Data_System_MRDS_Compact_Version/FeatureServer) | Global, strongest in U.S. | Frozen since 2011 | U.S. public domain | Fallback occurrences only. Ownership, status and resource fields are stale |
| [GSEU CRM hard rock deposits of Europe](https://zenodo.org/records/15234833) (`Data_CRM_deposits_ABC_2024.xlsx`) | 800+ medium to super-large deposits, 33 countries, sized by total endowment | Collected Apr–Oct 2024 | CC BY 4.0 | Primary European layer |
| [Geoscience Australia, Australian Critical Minerals Map 2025](https://data.gov.au/data/dataset/19e884f8-82d5-4fca-9ce3-58acec376b90) plus [ArcGIS MapServer](https://services.ga.gov.au/gis/rest/services/AustralianCriticalMineralsOperatingMinesAndDeposits/MapServer/info/iteminfo) | Operating mines, mines under development, care and maintenance, and resource deposits | Status as at Dec 2025 | CC BY 4.0 (stated on the service; confirm for the 2025 file) | Primary Australian layer; includes status |
| [Geoscience Australia global REE compilation](https://ecat.ga.gov.au/geonetwork/eng/api/records/bd8553a2-5150-45b3-920f-0ae8d92476bf) | Global REE deposits with substantial endowment | 2023 | Confirm | Cross-check for REE size data |
| Mindat | \~400,000 localities | Live | [Not licensed for redistribution](https://github.com/ChuBL/OpenMindat) | Internal cross-checking only. Never publish values from it |

**Facilities (operating mines and plants)**

| Source | Coverage | Currency | Reuse | Role |
| --- | --- | --- | --- | --- |
| [ICMM Global Mining Dataset](https://www.icmm.com/global-datasets/mining-metals-facilities) | v1.5: 7,104 publicly shareable of 12,000 mines and processing facilities, 47 commodities | 2025–26 | Confirm (license not stated on the page; S&P-derived rows are withheld) | Rung 5 backbone; smelters and refineries for Phase 2 |
| [Open database on global coal and metal mine production](https://zenodo.org/record/7369478) (Jasansky et al.) | 1,171 mines, 80 countries, 80 materials; includes ownership and reserves, every point source-linked | 2000–2021 | CC BY 4.0 | Production history and ownership seed |
| [WAPHA global metal mines database](https://datadryad.org/dataset/doi:10.5061/dryad.j3tx95xmg) | Active and inactive mine points: name, status, commodity, coordinates (public spreadsheets only) | May 2023 | Dryad (CC0 by Dryad policy; confirm) | Status cross-check |

**Country and policy context**

| Source | Coverage | Currency | Reuse | Role |
| --- | --- | --- | --- | --- |
| [USGS Mineral Commodity Summaries 2026 data release](https://www.sciencebase.gov/catalog/item/696a75d5d4be0228872d3bf8) (`MCS2026_Commodities_Data.csv`) | World production and reserves by country, 90+ commodities | 2025 data | U.S. public domain (2025 release marked CC0) | `country_commodity`; world shares |
| [OECD Inventory of Export Restrictions on Critical Raw Materials 2026](https://www.oecd.org/en/publications/oecd-inventory-of-export-restrictions-on-critical-raw-materials-2026_d5ca8f62-en.html) | 80 countries, 65 commodities | Covers 2009–2024 | Report CC BY 4.0; confirm terms of the database download | `export_restriction` baseline |
| Official announcements of measures since 2025 (e.g. China's Ministry of Commerce notices) | Measures not yet in OECD data | Live | Public official documents | Manually curated, grade A, each with URL |
| [AidData Chinese Financing for Transition Minerals](https://www.aiddata.org/data/aiddata-chinese-finance-for-transition-minerals-dataset-version-1-0) (v1.0; [v2.0](https://www.aiddata.org/data/aiddatas-chinese-financing-for-transition-minerals-dataset-version-2-0)) | Chinese official-sector loans and grants for extraction and processing; v1.0 tracks $56.9B, 2000–2021 | v2.0 current | ODC-By (attribution) | `china_official_finance`; links to mine sites |
| [2025 U.S. List of Critical Minerals](https://www.usgs.gov/news/science-snippet/interior-department-releases-final-2025-list-critical-minerals) | 60 minerals | Nov 2025 | Public domain | Commodity vocabulary flag `on_us_list` |

**Filings and entities (Phase 1)**

| Source | What it gives | Access rules | Role |
| --- | --- | --- | --- |
| SEC EDGAR | S-K 1300 technical report summaries (filed as Exhibit 96), 10-K/20-F/40-F property disclosures, ownership in annual reports | Free; declared User-Agent with contact email; [max 10 requests per second](https://www.sec.gov/os/accessing-edgar-data); full-text search covers 2001 onward and returns at most 10,000 hits per query | Automated discovery and download |
| SEDAR+ (Canada) | NI 43-101 technical reports, annual information forms | [Terms prohibit scraping or any automated means](https://systems.securities-administrators.ca/?p=4978) to reproduce multiple items | Manual retrieval only, or the same report from the issuer's own website |
| ASX announcements and issuer websites (Australia) | JORC resource statements, quarterly reports | Verify ASX terms before any automation; prefer issuer websites | Manual or issuer-site retrieval |
| Issuer websites and annual reports | Technical reports, ownership structure, offtakes | Respect robots.txt and terms | Primary evidence for non-U.S.-listed issuers |
| GLEIF LEI data, including parent relationships | Legal entity identifiers and direct/ultimate parents | Open (CC0) | Entity resolution and ownership chains |
| U.S. agency press releases (DFC, EXIM, DoD Office of Strategic Capital, DOE) | Announced support for named projects | Public domain | `us_support_tag` only |

## Phase 0: open-data deposit map

Goal: a private preview of a global map of rare earth, nickel and cobalt sites, each with a maturity rung and full provenance, plus mineral and country pages. Estimated 3 to 4 weeks. Every step lists what to do, what it produces, and when it is done.

1. **Scaffold the repository.**
   - Do: create the layout in Architecture; set up `uv`, `pytest`, `ruff`, pre-commit, GitHub Actions; add a `Makefile` with targets `fetch normalize harmonize link enrich qa publish site`.
   - Done when: `make all` runs end to end on empty inputs and CI is green.
2. **Register sources and write fetchers.**
   - Do: one YAML per Phase 0 source; one fetcher per source writing to `data/raw/<source>/<date>/` plus a manifest (URL, bytes, SHA-256, retrieved\_at). ArcGIS services must page through results (record caps of 1,000 to 2,000 per request).
   - Done when: a rerun with unchanged sources is a no-op, and the manifest covers every file.
3. **Build vocabularies.**
   - Do: `commodities.yaml` (code, name, group, `on_us_list`, per-source synonyms), `deposit_types.yaml`, `statuses.yaml`; ISO country codes via `pycountry` with an override list for source quirks.
   - Done when: 100% of source codes for REE, Ni and Co are mapped; unmapped terms are listed in the run report.
4. **Normalize each source to staging.**
   - Do: one parser per source producing `source_record` rows. Keep USGS REE unlocated records, flagged `location_precision = none`, off the map. Keep GSEU size classes, Geoscience Australia status, the USGS U.S. ranking, ICMM facility types and the Jasansky facility/sub-site structure as raw fields.
   - Done when: row counts reconcile with each source's documentation (for example about 3,100 located USGS REE records, 1,271 USGS U.S. deposits), and every reject has a reason.
5. **Filter to the pilot minerals.**
   - Do: keep records listing REE, Ni or Co as primary or byproduct commodity.
   - Done when: the report shows counts by source, commodity and country.
6. **Resolve entities into sites.**
   - Do: block candidate pairs by distance (10 km for exact or approximate points, 25 km for district precision) and shared commodity; compare normalized names (strip words like mine, deposit, project; fold diacritics and transliteration variants) with `rapidfuzz`; score with `splink`. Auto-merge at 0.95 or above, send 0.70 to 0.95 to the review queue, keep separate below 0.70. Mint ULIDs for new sites.
   - Hand-label a stratified gold set of 200 pairs before tuning.
   - Done when: auto-merge precision is at least 97% on the gold set, recall is reported, and the review queue is exported as CSV.
7. **Assign maturity rung and status.**
   - Do: apply the ladder rules with a per-source mapping table. Examples: USGS U.S. ranking to rungs; Geoscience Australia operating and development status; ICMM operating facilities to rung 5. Grade and tonnage without a known code is rung 2. Jasansky production ends in 2021, so it cannot by itself justify rung 5.
   - Done when: every site has a rung and the evidence row behind it, and a manual spot-check of 30 random sites agrees with the assignment in at least 27.
8. **Load country context.**
   - Do: load USGS MCS 2026 production and reserves for the pilot commodities; OECD export restrictions; AidData records, linked to countries and, where names and coordinates match, to sites.
   - Done when: world production shares per commodity add up to 100% within rounding, and the top producer for each pilot mineral matches the MCS chapter text.
9. **Run QA.**
   - Do: `pandera` schemas for every canonical table; coordinates in range and inside the stated country (Natural Earth boundaries, 25 km tolerance); duplicate checks; provenance on 100% of rows; a license gate that blocks any row whose source has `redistribution_ok = false` or is unconfirmed from publish outputs.
   - Done when: all checks pass or have a documented, approved exception.
10. **Publish data products.**
    - Do: CSV and GeoParquet per public table; one `.pmtiles` file with `sites` and `facilities` layers built by `tippecanoe` (minimal properties: id, name, rung, commodities, country, status); one JSON file per site, country and mineral page; `manifest.json` with a CalVer release tag (YYYY.MM.DD) and the source list.
    - Done when: the tiles file is under 50 MB and every page JSON validates.
11. **Build site v0.**
    - Do: map, site card, mineral pages (REE, Ni, Co), country pages for every country with at least one site, sources and methods page, downloads page. See Frontend spec.
    - Done when: Lighthouse performance is 90 or above on mobile, `axe` reports no serious accessibility issues, and every page prints cleanly.
12. **Run the demo test and hand off.**
    - Do: script five staffer questions (including the Phase 0 success test in Purpose) and time answers; deploy to an access-protected preview.
    - Done when: each answer takes under 60 seconds, and Raj signs off. Freeze the canonical schema.

## Phase 1: project pipeline and ownership

Goal: 150 to 300 development-stage and operating assets across the Phase 0 and Phase 1 minerals, each with a code-compliant resource or reserve, stage, owners with equity percentages, and announced U.S. support, all traced to documents. Estimated 6 to 10 weeks. Start only after Raj signs off Phase 0.

1. **Extend the schema.**
   - Do: add `project`, `resource_estimate`, `entity`, `ownership`, `entity_relation`, `document`, `us_support_tag` with `pandera` schemas and fixtures.
   - Done when: Raj approves the schema change (it unfreezes Phase 0's schema) and tests pass.
2. **Build the asset universe.**
   - Do: assemble candidates per mineral from (a) EDGAR full-text search for S-K 1300 technical report summaries naming each commodity; (b) Phase 0 sites at rungs 2 to 4; (c) development-status sites from Geoscience Australia and the USGS U.S. list of deposits with resources but no production; (d) issuers already named in AidData or the Jasansky ownership tables.
   - Rank by contained metal and stage; include assets outside China that are owned or financed by Chinese entities.
   - Output: `review/universe.csv` with the reason each asset is in.
   - Done when: Raj approves the list (stop point).
3. **Acquire documents.**
   - Do: automate EDGAR only (declared User-Agent, under 10 requests per second, cache everything). For SEDAR+, ASX and issuer websites, produce a manual fetch list; files land in `data/raw/docs/` with checksums and source URLs. Prefer the latest technical report, then the latest annual report or annual information form.
   - Done when: every universe asset has at least one grade A document or is marked "no public document."
4. **Extract fields.**
   - Do: convert PDFs and HTML to page-anchored text (`pymupdf`, `pdfplumber` for tables). Locate the resource and reserve statement, ownership, stage, study dates, target production and capex. Use an LLM with a strict JSON schema (`pydantic` models); every field must return a page number and a short supporting snippet, or null.
   - Validate automatically: normalize units (Mt, %, g/t, ppm, TREO); check contained metal against tonnage × grade within 2%; require an effective date and reporting code; reject totals that do not equal the sum of categories.
   - Done when: extraction runs on all documents and validation failures are in the review queue.
5. **Review by a human.**
   - Do: build a minimal review tool (a local Streamlit or Datasette page showing extracted value beside the PDF page). Review 100% of ownership and equity fields and any field naming a state-owned entity; review a 20% random sample of resource fields, rising to 100% for any document type whose error rate exceeds 2%. Decisions are written to `overrides/`.
   - Done when: the review queue is empty and error rates are logged.
6. **Build ownership chains.**
   - Do: record direct stakes from filings (property and joint-venture percentages); add parent and substantial-shareholder links from annual reports, SEC Schedules 13D/13G, ASX substantial holder notices, and GLEIF parent records. Compute effective ownership by multiplying along chains, to a maximum depth of 4, with an as-of date.
   - Mark an entity as state-owned only when an official source (a filing, an annual report, or a government list of state enterprises) states it, and store the percentage and the source.
   - Done when: each asset shows direct owners summing to 100% or an explained remainder (for example "public float").
7. **Record stage and timelines.**
   - Do: stage from the latest study; target first production exactly as stated, with the date of the statement. Flag targets older than 24 months as stale.
   - Done when: every project has a stage and a dated source.
8. **Tag announced U.S. support.**
   - Do: curate from agency press releases and official notices: agency, instrument, stated amount, date, URL. Mark `authorized` or `disbursed` only when an official document says so; otherwise `announced`.
   - Done when: each tag links to an official URL.
9. **Set up monitoring.**
   - Do: a daily job checking EDGAR submissions for tracked issuers; a weekly manual checklist for non-U.S. issuers. New documents create review items, never direct edits.
   - Done when: a test filing triggers a review item end to end.
10. **Update the site.**
    - Do: add resource table, stage, timeline, ownership panel (direct and effective stakes, with sources) and U.S. support tags to asset cards; add a pipeline view per mineral (assets by stage and target year).
    - Done when: Frontend acceptance checks pass for the new components.
11. **Audit accuracy.**
    - Do: a separate reviewer (a person or an agent with no access to first-pass outputs) re-extracts 30 random assets from the documents.
    - Done when: published ownership percentages match on at least 98% of fields and resource figures on at least 95%; fix the root cause of every mismatch before release.

## Phase 2: analysis layers

Goal: turn the data into answers for the four staffer jobs: concentration, alternatives if China restricts a mineral, ownership-adjusted control, and hidden byproduct dependencies. Estimated 6 to 8 weeks. All methods are deterministic, documented on a methods page, and reproducible from published data. No price or demand forecasting.

1. **Add the processing layer.**
   - Do: load smelters and refineries from ICMM; hand-curate midstream plants for the pilot minerals that open datasets miss (REE separation and metal/alloy plants, magnet plants, nickel HPAL and matte plants, cobalt refineries, gallium and germanium refineries). Use USGS Minerals Yearbook commodity chapters and company filings as sources, with stated capacity, owner and status.
   - Done when: each curated plant has a grade A or B source and an owner, and the methods page states coverage gaps plainly.
2. **Compute concentration metrics.**
   - Do: for each mineral and each stage (mining, refining where data exists), compute country shares, top-3 share, and Herfindahl-Hirschman Index from USGS MCS, for every year available in the MCS data releases.
   - Done when: figures reproduce MCS totals and a unit test covers the HHI calculation.
3. **Compute ownership-adjusted production.**
   - Do: allocate each facility's production (or capacity, labelled as such) to owners' home countries by effective equity from Phase 1. Report coverage as covered production divided by national production from MCS.
   - Publish a country-mineral figure only where coverage is at least 60%; always show coverage next to the number.
   - Done when: the totals for three spot-checked country-mineral pairs are traced by hand from facility rows.
4. **Build the "if China restricts X" view.**
   - Do: inputs are a mineral, a stage (mined, refined, or both) and a restricted share (default 100%). Output: remaining non-China supply at that stage; the non-China pipeline ranked by stage and stated target year; and byproduct constraints that limit a quick response.
   - Show ranges where capacity and actual output differ. Never present a single forecast date.
   - Done when: the view reproduces hand calculations for REE and gallium, and the methods page lists every assumption.
5. **Map byproduct dependencies.**
   - Do: a sourced edge list of host-to-byproduct relationships (for example gallium from alumina refining, germanium from zinc refining and coal fly ash, cobalt from copper and nickel), linked to host facilities outside China with stated capacity as potential recovery sites.
   - Done when: every edge has a source, and each mineral page shows its host commodities.
6. **Generate briefing exports.**
   - Do: a one-page PDF per country and per mineral (key numbers, top sites, owners, restrictions, sources, data date) and a CSV pack per page.
   - Done when: PDFs render identically in Chrome and Edge, under 1 MB each, with a visible "data as of" date.
7. **Review methods externally.**
   - Do: send the methods page and sample outputs to one or two outside minerals experts for review before public release.
   - Done when: comments are resolved or listed as known limitations, and Raj approves public launch (stop point).

## Frontend and UX

The site is map-first but never map-only: every view has a table equivalent, because staff often want a list they can paste into a memo.

**Pages**

| Page | Contents | Phase |
| --- | --- | --- |
| Map (home) | Global map; layer toggles for deposits, facilities, projects; filters; a list view of visible features with CSV export | 0, extended in 1 |
| Site page | Name, country, commodities, maturity badge with a one-line explanation, status, reporting-system badge, sources | 0 |
| Site page additions | Resource and reserve table, stage and stated timeline, owners (direct and effective), production history, U.S. support tags | 1 |
| Mineral page | World production and reserves with China's share, top producers, sites by rung, export restrictions, host commodities; later the pipeline chart and the "if China restricts" view | 0, 1, 2 |
| Country page | Sites by mineral and rung, production shares, restrictions, Chinese official finance (AidData), notable sites; later ownership and U.S. support | 0, 1, 2 |
| Sources and methods | Every source with license and date, the maturity ladder, evidence grades, known gaps, change log | 0 |
| Downloads | Versioned CSV and GeoParquet per table, with a data dictionary | 0 |
| About | Purpose, neutrality statement, who maintains it, how to report an error | 0 |

**Map behavior**

- Color points by maturity rung using a color-blind-safe sequential palette; facilities use a different marker shape. A legend explains each rung in plain words.
- Filters: mineral, rung, country, status; from Phase 1, owner home country, minimum effective stake by owner country, and U.S. support.
- Cluster points at low zoom; never plot centroid-only records.
- The URL encodes filters, map position and selection, so any view can be shared as a link.
- Static search (Pagefind) across sites, companies and countries.

**Rules for every page**

- A "data as of" date and release tag at the top.
- Every number links to its source and evidence grade on click.
- A print stylesheet: one clean page per country or mineral, with sources in the footer.
- Plain, neutral language. State facts ("Company X holds 51%"), never characterizations.

**Technical budgets**

- Initial JavaScript under 300 KB compressed; the map library loads only on map views. Non-map pages are static HTML and work without JavaScript.
- No cookies, no third-party scripts, no client-side analytics. Use server logs only, if anything.
- WCAG 2.1 AA. Test on current Chrome and Edge on Windows and on Safari on iPhone.

## Quality, accuracy and legal policy

The Atlas is only useful to Senate staff if they can cite it without embarrassment. These rules override speed.

**Accuracy**

- Every published value has a source URL, retrieval date and evidence grade. No exceptions.
- Machine outputs (record linkage, LLM extraction) never publish without passing the review thresholds in Phases 0 and 1.
- Corrections go through `overrides/` with a reason and reviewer, and appear in a public change log with the release tag.
- A "Report an error" link on every page goes to a monitored inbox. Acknowledge within 2 working days; fix or explain within 10.

**Sensitive claims**

- Report ownership as equity percentages and named holders with sources. Never use labels such as "China-linked" or "adversary-controlled."
- Mark state ownership only from an official source, and show the percentage.
- Do not name natural persons except controlling shareholders already disclosed in filings.
- Before public launch, send each company with a Chinese state-owned stake shown on the site a courtesy notice of the data and the correction route.

**Licensing and copyright**

- Publish only data whose license allows redistribution; enforce it with the license gate in QA.
- The sources page carries every required attribution: CC BY sources (GSEU, Geoscience Australia, Jasansky et al.), ODC-By (AidData), OpenStreetMap ODbL for the basemap.
- Technical reports and annual reports are copyrighted. Publish extracted facts with page citations and links; do not republish report text, figures or tables, and keep snippets internal.
- Respect each site's terms: no automated access to SEDAR+; verify ASX terms before any automation.

**Neutrality**

- No commentary on administration policy. U.S. support appears as dated facts with official links.
- Methods and assumptions are public, so any reader can reproduce a figure.

**Security**

- No secrets in the repository; use GitHub Actions secrets. Pin dependencies and enable automated dependency alerts.
- The preview site sits behind access control until Raj approves launch.

## Operations

Ship a versioned data release monthly, plus out-of-cycle releases for major events such as a new export control. Steady-state upkeep should take about half a day to a day per week.

| Input | Check | Action |
| --- | --- | --- |
| SEC EDGAR filings for tracked issuers | Daily, automated | New documents create review items |
| Non-U.S. issuer documents and news | Weekly, manual checklist | Add documents to the fetch list |
| Export-control and policy announcements | Weekly, manual | Add `export_restriction` rows with official URLs |
| U.S. agency announcements | Weekly, manual | Add `us_support_tag` rows |
| USGS Mineral Commodity Summaries | Annually (data release early in the year) | Refresh `country_commodity` |
| OECD export restrictions inventory | Annually | Refresh baseline; reconcile manual rows |
| USGS, GSEU, Geoscience Australia, ICMM, AidData datasets | When a new version is published; check quarterly | Re-run Phase 0 pipeline for that source; diff and review |
| Source URLs on the site | Monthly link check | Repair or replace dead links |

**Versioning**

- Each release gets a CalVer tag (YYYY.MM.DD), a frozen copy of all public files, and a change log listing added, changed and removed records.
- Older releases stay downloadable, so a staffer can cite the exact version they used.
- Keep private archive copies of every source document used as evidence, with checksums, in case the original moves.

**Monitoring**

- CI fails the release if any QA check fails or if published row counts change by more than 10% without a change-log note.
- Uptime check on the site and the tiles file.

## Risks, open questions and decisions

The biggest risks are accuracy on ownership, licensing on two facility sources, and scope creep back into document tracking. Each has a mitigation built into the steps above.

| Risk | Effect | Mitigation |
| --- | --- | --- |
| Wrong ownership or state-ownership claim | Reputational damage; staff stop citing the tool | 100% human review of ownership fields; official-source rule; courtesy notices; correction route |
| ICMM or Geoscience Australia 2025 license does not allow redistribution | Thinner rung 5 and Australian layers | Ingest but hold back via license gate; ask both publishers in week 1; fall back to Jasansky, WAPHA and the 2024 Geoscience Australia data |
| Bad merges in entity resolution | Duplicate or blended sites | Gold-set precision target; review queue; overrides |
| Stale global geology (2011–2018 compilations) | Misses recent discoveries | Phase 1 filings fill rungs 3 and 4; show each record's source date |
| National reporting systems read as weak evidence | Understates China and Russia deposits | `reporting_system` badge and methods note |
| Senate network blocks a domain or asset | Tool fails for its main user | Self-host everything; ask the staffer to load the preview from a Senate machine before launch |
| Scope creep into agreements or deal terms | Duplicates what staff already have | Exclusions list; Raj approval for any new layer |
| Maintenance burden after launch | Data goes stale | Monthly release cadence; automation for EDGAR; clear weekly checklist |

**Decisions for Raj**

- [ ] Branding: CSIS, Satnam Energy, or co-branded. Decide before any public launch.
- [ ] Pilot minerals: confirm REE, nickel and cobalt for Phase 0.
- [ ] Write to ICMM and Geoscience Australia about redistribution terms (week 1).
- [ ] Validate with the SFRC staffer: which of the four jobs matters most, whether they already have commercial data access, and whether country or mineral pages come first.
- [ ] Confirm with the staffer that a free public tool raises no gift-rule concerns.
- [ ] Domain name and hosting account ownership.
- [ ] Who handles the error-report inbox after launch.
- [ ] Funding path for upkeep (foundation grant for the public layer; separate paid analysis layer, if any).
