# Critical Minerals Atlas

A sourced, static reference for critical mineral deposits, maturity and ownership. The implementation follows [the supplied spec](IMPLEMENTATION_SPEC.md), in numbered order.

**Current status: Phase 0, Step 1 — repository scaffold.** No mineral records have been ingested. Empty output files are build artifacts, not evidence that a country or commodity has no deposits. No website has been deployed. The canonical schema remains provisional.

## Run locally

Requirements: Python 3.12, uv 0.12.0, Node 22.23.4+ and npm, Git and Make. The frontend pins its own Node runtime for consistent npm scripts; CI uses Node 22.23.4.

```sh
make all
make hooks
make preview
```

`make all` installs locked dependencies, runs lint/tests, executes each empty-input pipeline stage in order, checks the Astro project, and builds the local static site. The first install needs internet access; the data stages and built website do not. `make preview` binds only to `127.0.0.1` and serves the build on port 4321.

```sh
make setup-python    # uv sync --locked
make setup-site      # npm ci
make check           # Ruff and pytest
make data            # fetch → normalize → harmonize → link → enrich → qa → publish
make site            # frontend tests/type checks/build; requires make data first
```

Each required stage is also an independent Make target. It requires the preceding stage's receipt. Invalid, populated, unexpected, changed or missing inputs stop the scaffold. Real ingestion, linkage, licensing checks and spatial QA will be implemented at their specified steps.

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

No error-report email, data-fetch contact or branding has been invented. These are owner decisions before source fetching or launch. Local code is not assigned a new license by this scaffold; downstream source data will retain its own license and attribution requirements.
