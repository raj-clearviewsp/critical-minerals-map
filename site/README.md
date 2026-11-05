# Critical Minerals Atlas frontend

The standard build produces the empty public preview in `dist/`. The private build writes `.preview-dist/` and reads the versioned release from `../data/releases/2026.11.04-v0/products/atlas.json`. Set `ATLAS_RELEASE_PATH` to select another prepared private release. An explicitly selected missing or malformed release fails the build.

The private home maps reviewed groups and provisional site entries. Site, mineral and country profiles have direct, printable HTML routes. The original source explorer remains at `/records/`. When the default release is absent, the private home uses the earlier source dataset from `../data/preview/site-v1/dataset.json`. Source-only observations keep their original IDs and dates.

From the repository root:

```sh
make setup-site
make site
make site-private
make site-private-preview
```

From `site/`, run `npm run test:build-modes` to exercise empty, historical and reviewed builds with isolated synthetic inputs. Add `-- --transport` to check PMTiles byte ranges and direct site routes through the actual loopback launcher. The test script removes its synthetic builds on completion.

The home starts with a local geographic overview. Pan and zoom loads MapLibre and its worker. A URL with an explicit viewport restores the interactive map. The searchable result list includes entries without eligible coordinates. Filters, selection and viewport state are linkable. The resource-or-better REE query uses `/?mineral=REE&exclude=CHN&minimum=3` and requires resolved geography outside China. The optional PMTiles outline shows features from the smaller downloadable subset at closer zoom. Pagefind generates the full-text index locally. The citation links open the publisher only when selected.

The release adapter accepts `atlas-private-release-v1` with site summaries, source records, country and mineral context and declared downloads. Source evidence retains its publisher ID, reference URL, date, grade and note. Production families identify explicitly documented pilot production. The build copies only declared files below `products/downloads/` after checking their SHA-256 digests. The ordinary build explicitly disables private inputs.

Double-click `Open Atlas.command` to serve the prepared private build on an available loopback port. Keep its Terminal window open during use. The launcher serves PMTiles range requests and local WASM, map, search and download assets. The Natural Earth basemap attribution is in `public/maps/README.txt`.

To bind a specific local port, run `node scripts/open-preview.mjs --no-open --port 4387` from `site/`. An occupied port produces a visible error. The double-click launcher continues to select a free port.
