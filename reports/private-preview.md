# Private source-record website

## Outcome

The latest seven adjudications are imported and a functional private Atlas is built.
At the owner's request, website development proceeds alongside identity validation.
No additional review is needed to use this source-record preview. The formal
200-comparison validation still needs 167 new comparisons and resolution of seven
earlier relationships before model tuning and automatic entity consolidation.

The explorer contains 10,788 distinct source observations from 12 sources. It offers
a local interactive map, name and place search, mineral/country/source filters,
an optional broader-area filter, paginated results, source-linked details, URL state
and CSV export. The initial view shows 8,739 records; 2,049 explicit districts and
complexes are opt-in. Unknown entity scope remains labelled. The 246 verified
publisher-view duplicate pairs remain separate observations.

Open the prepared local site by double-clicking `Open Atlas.command` in the repository
root. It serves only the built private directory on a free loopback port and opens
the default browser. Keep that Terminal window open; Control-C stops it. Rebuild
with `make site-private`. No source data or site has been publicly deployed.

## Preserved review history

The supplied adjudication CSV has SHA-256
`74a39e69cc04f99bc02425234ab347c9dcb6910c6c9ce6503a0b108391085296`.
It was validated against its frozen seven-case packet before import. Its bytes are
preserved in `data/review/adjudication-v1/completed.csv`. All 40 prior events remain
unchanged; seven new events produce a 47-event history and 34 latest comparisons:
14 same-site, 11 different-site, two containment and seven unresolved. Human
answers take precedence over research proposals. There are no model fits, automatic
merges or new canonical identities.

The aggregate [audit receipt](private-preview-audit.json) records checksums, source
counts, implementation hashes and privacy boundaries. `make preview-data-check`
rebuilds and compares local artifacts; `make preview-data-receipt-check` verifies
the committed aggregate receipt without private inputs in CI.

## Presentation and data boundaries

The design uses cream and forest green, readable typography, restrained controls
and an accessible results table. Natural Earth country geometry and MapLibre's
renderer/worker are bundled locally. Citations are external links, but runtime map
assets, fonts and scripts require no external service. Sources and About pages
explain provenance and limits. The map is a low-resolution orientation layer;
it is not a detailed terrain or street basemap.

Records retain original source identifiers, coordinates, raw status, source dates
where known and reuse terms. Unknown country codes and coordinate precision remain
unknown. Coordinates are not used to invent administrative assignments. Historical
operating labels do not become current maturity claims. Resource classifications,
ownership, validated production totals and consolidated site pages remain future
work under the implementation spec.

Private observation JSON and private review history stay Git-ignored, as does
`site/.preview-dist/`. Standard builds explicitly disable private data and contain
zero observations and zero private source entries, even when local inputs exist.
CI uploads only `site/dist/`. The private build must not be uploaded as a public
release.

## Verification

- Python: 757 tests passed; Ruff lint and formatting passed.
- Frontend: 13 Node tests passed; Astro diagnostics reported zero errors, warnings
  or hints. Tests cover data isolation, filter intersections, geographic bounds,
  URL state, source links, escaped embedded JSON and CSV safety.
- Standard and private builds passed output verification: zero and 10,788 records
  respectively, with bundled map worker and local basemap.
- Browser: real map rendering, country/mineral/source filtering, name search,
  selected-record details and URL reload, broader-area toggle, 12-source catalog,
  and 390-pixel mobile layout checked. Mobile has no document-level horizontal
  overflow. No runtime console errors were observed during these checks.
- Imported CSV bytes and all earlier review events were independently verified;
  parent pipeline modules and accepted review artifacts remain unchanged.

The current private dataset is 8.94 MB before compression. Renderer plus worker
and client code total about 439 KiB gzipped; the renderer is a separate chunk.
This is a functional private preview, not acceptance of the spec's production
performance budget or completion of Phase 0. Public-release performance, remaining
data enrichment and entity-validation gates are still open.
