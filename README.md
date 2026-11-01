# Critical Minerals Atlas

A sourced, static reference for critical mineral deposits, maturity and ownership, based on [the supplied spec](IMPLEMENTATION_SPEC.md).

**Current status: Steps 1–5 complete; all three review rounds preserved; a functional private source-record website is available.** At Raj's request, site development now proceeds alongside identity validation. The private explorer covers 10,788 observations from 12 sources with map, search, filters, source details and CSV export. Source observations remain separate; canonical and published data remain empty. No website has been deployed. See the [private-preview report](reports/private-preview.md).

For the prepared local copy, double-click **Open Atlas.command**. It opens the built site in your browser and serves only on your computer; keep its Terminal window open. To rebuild it after code changes, run `make site-private`. A fresh checkout has no private observations and requires the verified local source and review artifacts to build this preview.

## Run locally

Requirements: Python 3.12, uv 0.12.0, Node 22.23.4+ and npm, Git and Make. The frontend pins its own Node runtime for consistent npm scripts; CI uses Node 22.23.4.

```sh
make all
make hooks
make preview
```

On a fresh checkout with no raw downloads, `make all` installs locked dependencies, runs lint/tests, executes each empty-input pipeline stage in order, checks the Astro project, and builds the local static site. The first install needs internet access; the data stages and built website do not. `make preview` binds only to `127.0.0.1` and serves the build on port 4321.

```sh
make setup-python    # uv sync --locked
make setup-site      # npm ci
make check           # Ruff and pytest
make vocab           # full terminology audit; requires verified local raw snapshots
make vocab-check     # validate retained audit and mappings; no raw files or network needed
make data            # fetch → normalize → harmonize → link → enrich → qa → publish
make site            # frontend tests/type checks/build; requires make data first
```

Each required stage is also an independent Make target. It requires the preceding stage's receipt. Invalid, populated, unexpected, changed or missing inputs stop the scaffold. Use `make fetch SOURCE=<source_id>` for Step 2 acquisition. `make normalize` selects the real source parsers when verified raw snapshots exist. Linkage and production QA remain gated to later steps; `make data` still refuses populated inputs. Use `make check site` to validate code and the empty public preview.

## Outputs and provenance

- `data/staging/<source_id>.parquet`: source-specific staging records, named and checksummed by `reports/normalization-audit.json`. The old zero-row `source_record.parquet` scaffold artifact is retained separately from this active inventory.
- `data/canonical/*.parquet`: nine zero-row Phase 0 table contracts.
- `data/publish/`: CSV/Parquet mirrors and a JSON-schema-validated manifest with SHA-256 checksums. Plain Parquet at this step; GeoParquet and real PMTiles arrive in Step 10.
- `reports/runs/`: per-stage JSON receipts with rows, rejects, hashes and explicit scaffold status.
- `reports/vocabulary-audit.json`, `vocabulary-observations.csv`, and `vocabulary-source-fields.json`: Step 3 audit receipt, source-field terminology and format evidence.
- `review/unmapped-vocabulary.csv`: observed terms requiring review, including deliberately unresolved geology and status labels.
- `site/dist/`: local static site build.
- [Step 1 run report](reports/phase0-step01.md), [Step 3 report](reports/phase0-step03.md), and [changelog](CHANGELOG.md).

The manifest's `release_tag` defaults to `2026.10.28`, the spec's date. It labels a scaffold build, not a data release. `data_as_of` is null because no observation data has been published by the scaffold. Identical reruns preserve bytes and modification times. Conflicting outputs are never overwritten. To experiment with a new build label, use a separate clean checkout; preserve existing outputs first.

## Layout

```text
sources/          Source registry and vocabulary (Step 2 onward)
pipeline/         Fetch, normalize, harmonize, link, enrich, QA, publish
schemas/          Build-manifest JSON contract
overrides/        Human-reviewed corrections and persistent IDs
review/           Human review queue and decisions
data/             raw, staging, canonical, publish (generated and ignored)
site/             Astro static site
reports/          Step reports; generated stage receipts in runs/
tests/            Pipeline safety and reproducibility tests
```

Nothing in canonical data is edited by hand. Raw downloads will be immutable and checksummed. No data license is inferred from availability: unconfirmed or restricted sources must remain out of public outputs.

## CI and step gates

GitHub Actions separates lint/tests, the data build, and the site build. After Python tests, `make vocab-check` validates the retained terminology audit against the committed vocabularies without downloading raw data. The site job consumes the exact data artifact from the data job. Actions use pinned revisions; uv and npm use committed lockfiles. Dependabot checks uv, npm and action updates weekly. There is no deployment workflow.

A step is complete only when its acceptance checks pass, its report and changelog are written, its commit is pushed, and GitHub CI is green. The next numbered step remains gated until then. Step 3 passed all checks and remote CI; Step 4 passed [remote CI](https://github.com/raj-clearviewsp/critical-minerals-map/actions/runs/41233471095), and Step 5 passed [remote CI](https://github.com/raj-clearviewsp/critical-minerals-map/actions/runs/41234601740). Step 6 cannot proceed to tuning until the 200-pair review is labelled. Phase 1 requires Raj's Phase 0 sign-off. Public deployment and uncertain-license use require the approvals listed in the spec.

Raj supplied `raj@satnamenergy.com` for the download User-Agent. Error-report inbox and branding remain owner decisions before launch. Local code is not assigned a new license by this scaffold; downstream source data will retain its own license and attribution requirements.

## Acquire registered sources

```sh
make sources
ATLAS_CONTACT=raj@satnamenergy.com make fetch SOURCE=usgs_laterites
ATLAS_CONTACT=raj@satnamenergy.com make fetch SOURCE=gseu_crm FETCH_FLAGS=--refresh
uv run --locked python -m pipeline.fetch --verify data/raw/usgs_laterites/2026-10-29
```

Each source YAML binds its own fetch configuration to the common file or ArcGIS adapter. Acquisition checks the source decision, explicit owner overrides, HTTPS, robots rules and server rate limits. ArcGIS is captured by verified object-ID batches (at most 1,000 per page); IDs, metadata and every page are retained. Failed acquisitions leave no accepted raw snapshot. Files are staged outside raw and atomically accepted only when every checksum/provenance check passes.

An unchanged rerun verifies the cache without rewriting it. `--refresh` rechecks remote bytes; unchanged content preserves the original manifest and retrieval timestamp, even when registry commentary has changed. Changed data cannot overwrite an existing daily snapshot. Raw dates and retrieval timestamps use UTC; source review dates use the owner's America/Los_Angeles date.

[Source decisions](docs/source-decisions.md), [manual acquisition list](review/manual-acquisition.md), and the [Step 2 report](reports/phase0-step02.md) distinguish successful captures from access blockers. Source acquisition is not normalization or permission to publish all raw materials. Attribution and the actual publisher terms stay attached to each source. No scheduled monitoring or public deployment has been enabled.

## Register browser downloads

At Raj’s request, the manual acquisition queue was completed through publisher browser downloads and one existing file in Downloads. See the [acquisition report](reports/manual-acquisition-2026-10-31.md) and [inventory](reports/manual-acquisition-files.csv). Original files remain in Downloads; checksum-verified copies are organized by source in `data/raw/`.

```sh
uv run --locked python -m pipeline.fetch --import-local intake.json
```

The intake follows `schemas/local-intake.schema.json`: source ID, acquisition method, original local files and names, publisher URLs, and actual retrieval times when known. Unknown retrieval times remain null; `registered_at` records when the verified copy entered the archive. No local paths or signed download tokens are retained in raw manifests. The importer validates publisher checksums and sizes where supplied, rejects incomplete files, checks archive CRCs, and accepts all files for a source atomically. Byte-identical reruns preserve the original archive and manifest. Historical manifest license fields describe evidence at acquisition; current registry decisions control future publication.

## Review source terminology

`make vocab` verifies the local raw snapshots and reads the observed commodity, country, deposit-type and status fields. It writes a reproducible audit and the unmapped-term review queue. It does not create staging or canonical records, assign maturity, convert resource units or publish source observations. The official retained USGS 2025 list supplies the individually named `on_us_list` membership.

Aliases are exact and source-aware after Unicode, whitespace and case normalization. Only reviewed field-specific delimiters and explicit composite mappings split commodity values. Raw wording and uncertainty qualifiers remain evidence. Generic clay does not imply ion adsorption; historical Producer and Active labels do not imply present operation. ISO country mappings use pinned `pycountry` and explained overrides; ambiguous geography remains unresolved.

After changing vocabularies, rerun `make vocab` against the verified archive before `make vocab-check`. The latter checks vocabulary and observation hashes and replays resolution from the retained audit, so CI can detect stale mappings without raw files. It cannot replace local verification of the original source snapshots. The Step 3 gate requires no unmapped pilot commodity associations; unresolved nonpilot terminology remains visible in the review queue. Final counts and validation are tracked in the [Step 3 report](reports/phase0-step03.md).

## Normalize source records

```sh
make normalize                 # local verified raw sources → per-source Parquet
make normalize-check           # verify local raw/staging hashes, schemas and counts
make normalize-receipt-check   # offline definitions/reconciliation check for CI
```

The [Step 4 report](reports/phase0-step04.md) distinguishes deposits, facilities, source groupings, financial records and country/policy observations. All original fields and row provenance are retained. Unlocated records stay unlocated; unknown retrieval times and coordinate accuracy remain explicit. The staging contract is in `pipeline/normalize/core.py`. No pilot filter, inferred maturity, entity merge or published observation is introduced by normalization.

## Filter pilot minerals

```sh
make pilot                 # normalized records → pilot records with source evidence
make pilot-check           # validate local source/pilot bytes, schemas and counts
make pilot-receipt-check   # offline reconciliation and count-report verification
```

The [Step 5 report](reports/phase0-step05.md) and CSV count reports show records by source, mineral, host country and record kind. A record can list several commodities; source overlap remains until entity resolution. Unranked source associations retain their original status, uncertainty and evidence. Financing collateral and waste-only mineral content do not qualify records. The original normalized tables remain intact.

## Original identity review (retained for provenance)

```sh
make link-review                # requires verified local pilot/source data
make link-review-check          # regenerate and compare the private packet
make link-review-receipt-check  # aggregate receipt checks only; works in CI
uv run --locked python -m pipeline.link --labels /absolute/path/to/decisions.csv
```

The original `data/review/linkage/index.html` packet remains unchanged. The active workflow is the focused triage pilot below. Original exports select Same site, Different sites or Uncertain and require a reviewer name. Partial exports are supported; add `--partial` to validate them. Uncertain labels need adjudication before tuning. Keep exports separate from the immutable original packet. Browser storage is not a backup, so export progress regularly.

The detailed packet stays local and Git-ignored because source reuse terms differ. The committed [preparation receipt](reports/linkage-review-audit.json) contains aggregates and checksums only. There are no model predictions, trained scores, merges or new canonical site IDs. The [Step 6 preparation report](reports/phase0-step06-preparation.md) documents scope, sampling and evaluation limits. Steps 7–12 remain gated by Step 6 acceptance.

## Source-aware triage and focused review pilot

```sh
uv run --locked python -m pipeline.triage --decisions /absolute/path/to/original-review.csv
make triage                 # rerun using the preserved original CSV
make triage-check           # rebuild and compare all local evidence and outputs
make triage-receipt-check   # aggregate/report checks without private inputs
uv run --locked python -m pipeline.triage --labels /absolute/path/to/new-pilot.csv
```

Open `data/review/triage-v2/index.html` for the active 20-case pilot. Its local map, entity-level distinctions, typed aliases, source notes and record references support six explicit identity/relationship answers. Six structurally flagged prior cases retain their original answers read-only, and fourteen cases are new. New decisions start blank and export separately. The original CSV and all twenty prior labels, reviewer names and notes are preserved exactly under `data/review/triage-v2/`.

The [triage report](reports/phase0-step06-triage.md), [count export](reports/triage-counts.csv) and [audit receipt](reports/triage-audit.json) explain all routes. Verified GA publisher views share reviewer questions while retaining every source observation and original candidate pair. Private evidence, source records and reviewer notes stay Git-ignored. The 20-case usability pilot is not the formal 200-pair gold evaluation, and triage rules do not fit a model or authorize automatic merges.

## Completed pilot and formal validation preparation

The completed pilot is preserved as a second immutable review round. The [reconciliation report](reports/phase0-step06-reconciliation.md) records the outcomes, primary-source research and frozen 200-comparison design. Open `data/review/reconciliation-v1/index.html` locally for seven evidence adjudications, 87 new development cases and 80 blinded holdout cases. The design retains 33 prior site comparisons; a duplicate publisher-view comparison remains in history.

```sh
make reconcile                 # deterministic rebuild from preserved local inputs
make reconcile-check           # rebuild and compare all private artifacts
make reconcile-receipt-check   # offline code/aggregate verification used in CI
uv run --locked python -m pipeline.reconciliation --labels /absolute/path/to/export.csv --packet adjudication
```

Use `--packet development` or `--packet holdout` for those exports; `--complete` checks that every case was answered. Export validation does not import or overwrite decisions. Initial preparation accepts `--decisions` and `--evidence` paths; all subsequent reruns use the preserved copies. Research proposals remain separate from human labels. Unresolved scope and source crosswalks remain explicit.

Whole connected candidate neighborhoods stay within one development/holdout split. The review pages omit automated routing, and the holdout also omits past answers and research proposals. The 200-case review is still pending; no model is fitted, thresholds tuned or sites merged by this checkpoint.

## Researched first 60 development comparisons

At Raj's request, the first 60 new development cases now have detailed agent
recommendations and a separate second review. Open
`data/review/development60-v1/index.html` to approve or revise individual proposals,
or `analysis.html` in that directory to read/print the complete dossier. Each human
answer starts blank. The packet preserves the original pair order and exact source
observations. The other 27 development and 80 blinded holdout cases are unchanged.

```sh
make research-review-check         # reproduce the full local research packet
make research-review-receipt-check # aggregate receipt validation for CI
uv run --locked python -m pipeline.research_review --labels /absolute/path/to/export.csv --complete
```

Omit `--complete` to validate partial progress. Validation does not import answers.
The [batch report](reports/development60-review.md) explains proposal counts,
reviewer assistance and the remaining validation work. Detailed research and source
records remain private; only aggregate counts and checksums are committed.
