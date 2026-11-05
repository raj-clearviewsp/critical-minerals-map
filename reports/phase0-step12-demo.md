# Phase 0 private Atlas walkthrough

The private release is ready for the owner's review on the prepared computer. Open `Open Atlas.command` in the Atlas project folder. The current local preview is at `http://127.0.0.1:4387/`. The [website report](phase0-step11-site.md) covers accessibility, performance, print and browser compatibility.

Five scripted questions were timed in the desktop app's browser on November 5, 2026. Each began on the loaded Explore page and ended when the answer was visible. An agent operated the browser using ordinary navigation and controls. Times include browser interaction and observation. First-time staff use and owner sign-off are separate acceptance steps.

| Staff question | Path and observed answer | Elapsed |
| --- | --- | ---: |
| Where are rare earth deposits outside China, how many have a code-compliant resource or better, and who operates the producing ones? | The outside-China shortcut returned 11 entries at resource stage or above, in Australia and the United States. Mount Weld lists Lynas Rare Earths Limited and Mountain Pass lists MP Materials Corp. Both have documented rare-earth production. Selecting all stages with the same geography showed 816 mapped entries. | 5.2 s |
| What reserve standard and date support the Nolans Bore assessment? | Search for Nolans Bore, open a profile and follow the reserve evidence. Arafura's FY2026 report reaffirms a JORC 2012 proved and probable reserve as of June 30, 2026, with the report pages and evidence grade visible. | 9.7 s |
| How much nickel did Indonesia mine in 2025, and what was its world share? | Countries → Indonesia. The USGS estimate is 2.6 million tonnes of nickel content, or 66.7% of world mine production. The reported values link to the nickel chapter. | <1 s |
| Which country supplied the largest share of cobalt in 2025? | Minerals → Cobalt. Congo (Kinshasa) accounted for 74.2% of an estimated 310,000 tonnes of world cobalt mine production. The year, unit and USGS chapter are visible. | <1 s |
| Which geographic site file can a researcher download, and how much of the map does it cover? | Downloads → Site entries · GeoParquet. It contains 282 entries that passed the distribution checks. The private map contains 1,220 site entries. The file served by the preview matched the release checksum. | <1 s |

The 11 advanced entries have six distinct displayed names. Some are alternate records published for the same place. Maturity describes the strongest supported stage of a site. A listed mineral at an operating site requires separate evidence to be labelled as produced. Seventy-three unresolved comparisons remain explicitly marked. Automatic merging is disabled in this release.

## Handoff

- The private ZIP is in the enclosing outputs folder as `critical-minerals-atlas-private.zip`. It contains 1,626 files and 1,334 HTML pages. The ZIP passed integrity checks and its SHA-256 is `e94a9be10b8aaf58a49c78622e9fef2048be2f90d739096f4d3f814d0c5c9d80`.
- All 21 download files matched the approved release. The browser checks covered every country and mineral brief for printing. All 143 artifacts covered by the earlier review-preservation check retained their recorded contents and modification times.
- Chrome and Edge passed the [Windows browser run](https://github.com/raj-clearviewsp/critical-minerals-map/actions/runs/42040198731). The [repository checks](https://github.com/raj-clearviewsp/critical-minerals-map/actions/runs/42040198897) also passed. A physical iPhone Safari check remains open.
- The preview is available on loopback. An online private preview requires the intended Cloudflare Pages project, the authorized viewer group and an access policy verified before upload. The package stays on this computer pending that configuration.
- The Step 10 release preserves the candidate canonical schema and data contracts for review. The formal schema freeze and Phase 1 require Raj's Phase 0 sign-off.
