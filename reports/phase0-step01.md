# Phase 0 · Step 1 run report

Implementation date: October 28, 2026 (America/Los_Angeles). Build label: `2026.10.28`.

## Scope

Repository scaffold only. Added required directories, ordered Make targets, Python 3.12/uv tooling, pytest, Ruff, pre-commit, separate data/site CI jobs and an Astro static scaffold. The supplied implementation spec is retained in the repository.

## Data accounting

| Item | Count | Explanation |
| --- | ---: | --- |
| Sources fetched | 0 | Source registration and fetchers belong to Step 2 |
| Raw files downloaded | 0 | No source acquisition attempted |
| Staging rows | 0 | Empty source-record contract |
| Canonical tables | 9 | Provisional Phase 0 tables, all zero rows |
| Published data rows | 0 | Empty build artifacts, not a public release |
| Rejects | 0 | No records submitted; invalid synthetic test cases fail explicitly |
| Sites / facilities | 0 / 0 | No invented points or factual claims |

Generated per-stage reports are in `reports/runs/`. They contain row counts, rejects and output checksums. `data_as_of` is null. No source retrieval dates, evidence grades or coverage claims have been fabricated.

## Verification

- `make all`: passed, including locked installs, Ruff, all seven data stages and the static site build.
- Python: 43 pytest checks passed, including deterministic no-op reruns, preserved raw/override files, schema failures, missing checksums, invalid sequencing and refusal to overwrite data.
- Frontend: 3 manifest tests passed; Astro reported 0 errors, 0 warnings and 0 hints.
- Built HTML verification: no client JavaScript or remote assets; empty-data notice and print CSS present.
- Local preview: HTTP 200 and browser inspection passed at a narrow viewport. Full cross-browser, Lighthouse, axe and PDF checks belong to Step 11 and have not been claimed here.
- `npm audit`: 0 vulnerabilities after updating to Astro 7.3.8.
- Pre-commit installed with locked Ruff hooks.
- Initial scaffold pushed as `5dfc8254b871f77417d38b29b4439a01ece5576d`. GitHub rejected a job-level `runner.temp` expression before jobs started; removed that unsupported context and retained Make/setup-uv cache configuration. The repaired workflow is awaiting verification. Step 2 remains gated until it succeeds.

## Open issues and deferred requirements

- All substantive source processing and the production schema/QA rules are deferred to their numbered steps. Step 1 contracts deliberately reject nonempty frames.
- No PMTiles, map, production charts, ownership analysis or actual source downloads are included at this step.
- A contact User-Agent is required before Step 2 downloaders can fetch real data; no contact has been inferred from Git identity.
- Unclear dataset licenses require Raj's decision before use. No such values have been acquired.
- Phase 0 completion, human gold-set review, maturity spot-check, protected preview approval and Phase 1 authorization remain pending.
- No deployment performed. GitHub repository is public as supplied by Raj; it holds code only.
