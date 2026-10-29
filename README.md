# Critical Minerals Atlas

A sourced, static reference for critical mineral deposits, maturity and ownership. The implementation follows [the supplied spec](IMPLEMENTATION_SPEC.md), in numbered order.

**Current status: Phase 0, Step 1 complete; Step 2 acquisition underway.** Raw downloads are being captured, while canonical tables and the site remain empty. Empty output files are build artifacts, not evidence that a country or commodity has no deposits. No website has been deployed. The canonical schema remains provisional.

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
make data            # fetch → normalize → harmonize → link → enrich → qa → publish
make site            # frontend tests/type checks/build; requires make data first
```

Each required stage is also an independent Make target. It requires the preceding stage's receipt. Invalid, populated, unexpected, changed or missing inputs stop the scaffold. Use `make fetch SOURCE=<source_id>` for Step 2 acquisition. Normalization, linkage and production QA remain gated to later steps. Once raw data exists, the empty scaffold correctly refuses to normalize it; use `make check site` to validate code and the existing empty preview.

## Outputs and provenance

- `data/staging/source_record.parquet`: zero-row staging contract.
- `data/canonical/*.parquet`: nine zero-row Phase 0 table contracts.
- `data/publish/`: CSV/Parquet mirrors and a JSON-schema-validated manifest with SHA-256 checksums. Plain Parquet at this step; GeoParquet and real PMTiles arrive in Step 10.
- `reports/runs/`: per-stage JSON receipts with rows, rejects, hashes and explicit scaffold status.
- `site/dist/`: local static site build.
- [Step 1 run report](reports/phase0-step01.md) and [changelog](CHANGELOG.md).

The manifest's `release_tag` defaults to `2026.10.28`, the spec's date. It labels a scaffold build, not a data release. `data_as_of` is null because nothing has been retrieved. Identical reruns preserve bytes and modification times. Conflicting outputs are never overwritten. To experiment with a new build label, use a separate clean checkout; preserve existing outputs first.

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

GitHub Actions separates lint/tests, the data build, and the site build. The site job consumes the exact data artifact from the data job. Actions use pinned revisions; uv and npm use committed lockfiles. Dependabot checks uv, npm and action updates weekly. There is no deployment workflow.

A step is complete only when its acceptance checks pass, its report and changelog are written, its commit is pushed, and GitHub CI is green. Step 2 does not begin before that gate. Phase 1 requires Raj's Phase 0 sign-off. Public deployment and uncertain-license use require the approvals listed in the spec.

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
