# Manual acquisition queue — Phase 0 Step 2

These sources have publicly readable metadata but their file-hosting rules or access responses prevent the current automated downloader from acquiring the needed files. The downloader does not change identity, use proxies, or invent alternate URLs to bypass those restrictions. Acquire the publisher's original file through a permitted human download, retain the original filename and URL, and provide it for checksum registration. Do not edit the file or place hand-edited data in canonical tables.

## Core files still required

| Source | Publisher page | Required material | Access reason |
| --- | --- | --- | --- |
| USGS global REE | [ScienceBase record](https://www.sciencebase.gov/catalog/item/5a0c5a0de4b09af898cd15d2) | Original geodatabase/archive including located records, nonspatial table and references | ScienceBase robots explicitly disallow automated access |
| USGS global critical minerals 2017 | [ScienceBase record](https://www.sciencebase.gov/catalog/item/594d3c8ee4b062508e39b332) | Original point/polygon dataset and supporting metadata | ScienceBase and the identified mrdata alternative disallow automated access |
| USGS MCS 2026 | [ScienceBase record](https://www.sciencebase.gov/catalog/item/696a75d5d4be0228872d3bf8) | `MCS2026_Commodities_Data.csv` | ScienceBase robots explicitly disallow automated access |
| GA global REE | [Official compilation](https://pid.geoscience.gov.au/dataset/ga/148953) | `148953_01_0.xlsx` and report `148953_00_0.pdf` | The official CloudFront file host disallows automated access |
| GA 2025 workbook | [Official dataset](https://data.gov.au/data/dataset/19e884f8-82d5-4fca-9ce3-58acec376b90) | `150802_01_0.xlsx` | The official CloudFront file host disallows automated access; Raj approved acquisition on October 28 |
| USGS MRDS fallback | [Publisher service metadata](https://services.arcgis.com/v01gqwM5QqNysAAi/ArcGIS/rest/services/Mineral_Resources_Data_System_MRDS_Compact_Version/FeatureServer) | A publisher-permitted original export with commodity/name support tables where available | Broad Esri website terms on systematic harvesting require clarification for this bulk snapshot; robots403 alone is not a prohibition |
| WAPHA | [Dryad dataset](https://datadryad.org/dataset/doi:10.5061/dryad.j3tx95xmg) | Public active/inactive mine spreadsheets only | API download requires authentication; public download path is robots-disallowed |

Exact unverified USGS archive filenames have intentionally not been guessed. MCS's known publisher checksum and the WAPHA public spreadsheet SHA-256 values are retained in the source registries/metadata audit for verification after acquisition.

Individual government export announcements are a manual document collection, not a single downloadable source. Each future row requires its own dated official announcement and checked reuse terms; no announcement facts are invented at this step.

Mindat remains excluded under the spec. Raj's later approval named ICMM, AidData v2 and the GA workbook; it does not authorize extraction of the Mindat database.
