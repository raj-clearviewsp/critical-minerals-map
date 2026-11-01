# Atlas explorer

The Astro site has a standard empty build and a separate local private build.
The private explorer shows source observations, not consolidated sites. It supports
map and list views, search, mineral/country/source/scope filters, source details,
URL state and filtered CSV export. Explicit district and complex records are
hidden by default; unknown scope remains labelled. Maturity is unassigned and
source status is presented as historical wording.

From the repository root:

```sh
make setup-site
make site                 # tests, Astro diagnostics, empty standard build
make preview-data-check   # verify private data and preserved review history
make site-private         # verify inputs, test and build private explorer
make site-private-preview # serve on loopback
```

For the prepared local copy, double-click `Open Atlas.command`. It serves the
already-built private site on a free loopback port and opens the default browser.
Keep its Terminal window open while using the site; Control-C stops the server.

Astro, MapLibre GL JS and Node are pinned in package-lock.json. MapLibre is loaded
as a separate client chunk. The Natural Earth country geometry is local and
public-domain; provenance is in public/maps/README.txt. There are no remote fonts,
map tiles, scripts, analytics or automatic network data refreshes. Publisher
citations are ordinary external links opened only when selected.

`npm run build` explicitly disables private data and writes `dist/`. CI uploads
only that empty build. `npm run build:private` explicitly enables the verified
local dataset and writes the ignored `.preview-dist/` directory. The private
JSON is read only in that mode, from ../data/preview/site-v1/dataset.json. Build
verification checks embedded counts, source isolation and local runtime assets.
The private build must not be uploaded as a public release.

The private pages include noindex metadata, readable responsive typography,
keyboard-accessible controls, a list fallback when WebGL is unavailable and print
styles. JSON is escaped before embedding in HTML. Source and reviewer decisions
are prepared independently by `pipeline.preview`; reviewer names and notes do
not enter the site dataset.
