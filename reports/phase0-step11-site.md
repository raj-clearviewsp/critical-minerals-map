# Phase 0 website

The private Atlas opens on a map of 1,220 site entries. Readers can switch to the list, filter by mineral, country and maturity, open an entry's evidence, or use full-text search. The release includes 89 country profiles, three mineral profiles and a directory of 21 downloadable products. The full archive retains 10,788 located source observations. A publisher's alternate views can describe the same place. The site identifies those entries and keeps unconfirmed operators explicit.

The home page shows the local basemap before loading the interactive map. Enabling pan and zoom loads the map engine. The shaded points show maturity and the legend gives the stage names. For a selected mineral, operating entries say whether its production is documented. Readers can share the list, point selection, zoom and location through the URL. On small screens, navigation and filters expand on demand.

## Build checks

- Built and indexed 1,334 static pages. All 40,641 checked internal page, asset and anchor references resolved on the final build.
- Verified the 21 downloadable files against the release manifest. The bulk site table contains 282 entries cleared for redistribution. Readers can see its coverage separately on the map.
- Validated normal, source-only and reviewed-release build modes. The standard build contained zero private entries on a machine with the private release present.
- Passed 22 frontend tests and Astro diagnostics. Confirmed byte-range requests for PMTiles, including partial requests, HEAD responses and invalid ranges.
- Confirmed that enabling pan and zoom changes the visible-area result count, and that the phone layout exposes the same filters through its disclosure.

## Browser, access and print checks

Mobile Lighthouse rated the home page 98/100 on three cold runs. The site, mineral and country profiles each scored 100/100. Home first contentful paint was 1.35 seconds and largest contentful paint was 2.25 seconds under Lighthouse mobile simulation. The home loaded 8,859 compressed JavaScript bytes initially. The optional map engine loads when a reader requests it.

All 13 representative templates fit widths of 390, 549 and 1,280 pixels. The automated WCAG 2.1 A/AA checks reported zero violations. The pages made zero external runtime requests. Chrome printed all 89 country and three mineral briefs on one A4 page each. Printed tables identify their sources by title. Other page types retain longer paginated lists where needed. See the [aggregate browser measurements](phase0-step11-site-audit.json).

The private release was tested in Chrome on macOS and the desktop app's browser. The Windows CI workflow exercises actual Chrome and Edge with synthetic data. Physical iPhone Safari verification is outstanding.

The first Windows run found an 11-pixel overflow on a narrow synthetic country profile. The compact table headers now stay within their allocated space. The test retains its original 390-pixel limit and now reports which elements extend beyond it. The country directory also distinguishes context-only profiles and uses singular site counts where appropriate.

## Reproduce the private preview

Open `Open Atlas.command` in the prepared repository. The launcher serves the built private site on the local computer. The versioned product manifest and the private build are local release artifacts. CI builds from synthetic fixtures or the empty public input. The [Step 10 report](phase0-step10-products.md) documents the underlying tables and distribution limits.
