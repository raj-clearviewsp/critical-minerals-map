# Atlas site scaffold

This is the static Astro foundation for Phase 0 Step 1. It renders the pipeline's
empty publish manifest and deliberately refuses nonempty data until ingestion and
publication are implemented and verified in the later numbered steps.

From the repository root, run `make setup` and `make all`. The root pipeline writes
`data/publish/manifest.json` before the site builds. A missing or malformed manifest
fails the build rather than substituting sample mineral records.

Astro 7.3.8 and the development tools are pinned. A repository-local Node 22.23.4
runtime is installed by npm and used by npm scripts; the system Node installation
is unchanged. CI should use Node 22.23.4 or a newer supported LTS.

Inside `site/`:

- `npm ci` installs the exact versions in `package-lock.json`.
- `npm run check` runs the manifest contract tests and Astro diagnostics.
- `npm run build` generates `dist/` and verifies the static output.
- `npm run preview -- --port 4321` serves the build on loopback only.

The Step 1 page includes no client JavaScript, analytics, remote fonts or images.
Responsive and print styles are self-hosted. Build telemetry is disabled in npm
scripts. The generated page has `noindex, nofollow`; it is a local development
artifact, not a public data release or the Phase 0 map application.

The manifest supplies `release_tag`, `data_as_of`, `status`, `sources` and `tables`.
The Step 1 contract requires `status: scaffold`, `data_as_of: null`, no sources,
and zero rows in each declared table. It does not infer a data date from a build
date. All operational paths remain in build scripts rather than the user flow.
