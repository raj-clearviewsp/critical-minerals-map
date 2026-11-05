from __future__ import annotations

import tempfile
from pathlib import Path

import jsonschema
import pandas as pd
import pandera.pandas as pa

from .canonical import canonical_files
from .catalog import assemble
from .common import (
    BASE,
    PRODUCTS,
    QA,
    TAG,
    encoded,
    load,
    metadata,
    required_inputs,
    seal,
    sha,
    verify,
)
from .downloads import IDENTIFIERS, TABLES, build_downloads
from .quality import known_date, safe_url

DIRECTORY = f"{BASE}/products"
REPORT = "reports/phase0-step10-products.md"


def validate_tables(tables):
    for name, rows in tables.items():
        fields = TABLES[name]
        required = set(IDENTIFIERS.get(name, ()))
        required.update(
            ("url", "license", "redistribution_ok", "checksum")
            if name == "source"
            else (
                "source_id",
                "source_url",
                "retrieved_at",
                "evidence_grade",
                "raw_sha256",
                "evidence",
            )
        )
        checks = {
            "retrieved_at": pa.Check(known_date, element_wise=True),
            "url": pa.Check(safe_url, element_wise=True),
            "source_url": pa.Check(safe_url, element_wise=True),
            "evidence_grade": pa.Check.isin(["A", "B", "C", "D"]),
            "redistribution_ok": pa.Check.eq(True),
            "lat": pa.Check.in_range(-90, 90),
            "lon": pa.Check.in_range(-180, 180),
            "maturity_rung": pa.Check.in_range(1, 5),
        }
        schema = pa.DataFrameSchema(
            {k: pa.Column(None, checks.get(k), nullable=k not in required) for k in fields},
            strict=True,
            unique=list(IDENTIFIERS.get(name, ())) or None,
        )
        schema.validate(pd.DataFrame(rows, columns=fields), lazy=True)


def write(root: Path, tool: Path):
    verify(root, QA, local=True)
    verify_supplement(root)
    atlas, tables, production_review = assemble(root, with_audit=True)
    validate_tables(tables)
    schemas = load(root, "schemas/release-page.schema.json")["$defs"]
    outputs = canonical_files(root, atlas, tables)
    outputs[f"{DIRECTORY}/production-reconciliation.json"] = encoded(production_review)
    counts = {k: len(v) for k, v in tables.items()}
    with tempfile.TemporaryDirectory(prefix="atlas-products-") as temp:
        dest = Path(temp)
        downloads = build_downloads(tables, dest, tool)
        for descriptor in downloads:
            p = descriptor["path"]
            outputs[f"{DIRECTORY}/{p}"] = (dest / p).read_bytes()
        release_sources = [
            {
                k: r.get(k)
                for k in ("source_id", "name", "url", "license", "attribution", "retrieved_at")
            }
            for r in tables["source"]
        ]
        public_manifest = encoded(
            {
                "release_tag": TAG,
                "compiled_on": "2026-11-04",
                "sources": release_sources,
                "tables": counts,
                "files": [
                    {**item, "path": item["path"].removeprefix("downloads/")} for item in downloads
                ],
                "paths_relative_to": "manifest.json directory",
                "scope": (
                    "Downloadable site entries passed membership, geography, source permission "
                    "and retrieval evidence checks."
                ),
            }
        )
        p = "downloads/manifest.json"
        outputs[f"{DIRECTORY}/{p}"] = public_manifest
        atlas["downloads"] = [
            *downloads,
            {
                "path": p,
                "label": "Release manifest",
                "format": "JSON",
                "table": "manifest",
                **metadata(public_manifest),
                "description": "Version, sources, counts and checksums.",
            },
        ]
    for section, kind, key in [
        ("sites", "site", "id"),
        ("countries", "profile", "code"),
        ("minerals", "profile", "code"),
    ]:
        validator = jsonschema.Draft202012Validator(schemas[kind])
        seen = set()
        for row in atlas[section]:
            validator.validate(row)
            identifier = row[key]
            if identifier in seen:
                raise ValueError("Duplicate release profile ID")
            seen.add(identifier)
            outputs[f"{DIRECTORY}/pages/{section}/{identifier}.json"] = encoded(
                {"release_tag": TAG, "data_as_of": atlas["data_as_of"], **row}
            )
    outputs[f"{DIRECTORY}/atlas.json"] = encoded(atlas)
    # Build the distribution directory solely from the QA-approved projections.
    publish = f"data/publish/{TAG}"
    for entry in atlas["downloads"]:
        relative = entry["path"]
        outputs[f"{publish}/{relative.removeprefix('downloads/')}"] = outputs[
            f"{DIRECTORY}/{relative}"
        ]
    for row in tables["site"]:
        outputs[f"{publish}/pages/sites/{row['site_id']}.json"] = encoded(
            {"release_tag": TAG, "data_as_of": atlas["data_as_of"], **row}
        )
    for profile in atlas["countries"]:
        code = profile["code"]
        out = {
            "release_tag": TAG,
            "data_as_of": atlas["data_as_of"],
            "code": code,
            "name": profile["name"],
            "site_ids": [s["site_id"] for s in tables["site"] if s["country_iso3"] == code],
            "statistics": [r for r in tables["country_commodity"] if r["country_iso3"] == code],
            "restrictions": [r for r in tables["export_restriction"] if r["country_iso3"] == code],
            "contributions": [
                r for r in tables["china_official_finance"] if r["country_iso3"] == code
            ],
        }
        if any(out[k] for k in ("site_ids", "statistics", "restrictions", "contributions")):
            outputs[f"{publish}/pages/countries/{code}.json"] = encoded(out)
    for profile in atlas["minerals"]:
        code = profile["code"]
        out = {
            "release_tag": TAG,
            "data_as_of": atlas["data_as_of"],
            "code": code,
            "name": profile["name"],
            "site_ids": [s["site_id"] for s in tables["site"] if code in s["commodities"]],
            "statistics": [r for r in tables["country_commodity"] if r["commodity"] == code],
        }
        outputs[f"{publish}/pages/minerals/{code}.json"] = encoded(out)
    pages = {k: len(atlas[k]) for k in ("sites", "minerals", "countries")}
    c = {
        "page_counts": pages,
        "table_counts": counts,
        "downloads": len(atlas["downloads"]),
        "pmtiles_bytes": len(outputs[f"{DIRECTORY}/downloads/atlas.pmtiles"]),
        "source_archive_observations": 10788,
        "site_entries_with_operator": sum(bool(s.get("operator")) for s in atlas["sites"]),
        "publication_production_corrections": len(production_review["corrections"]),
    }
    report = f"""# Phase 0 release products

The private Atlas has {pages["sites"]:,} geographically checked site entries, {pages["countries"]}
country profiles and three mineral profiles. Each page has a validated JSON record. The private
source archive retains 10,788 located observations.

The distributable tables contain {counts["site"]:,} site entries and {counts["facility"]:,} mine
facility records. The download set includes CSV, Parquet, GeoParquet, a data dictionary and a
versioned manifest. The PMTiles archive is {c["pmtiles_bytes"]:,} bytes and has site and facility
layers. Each export has a checksum, row count, field contract, source attribution and release tag.
Empty tables retain their column definitions. Geographic, reuse and provenance holds are documented
in the Step 9 report. Holdings in the private source archive can exceed downloadable coverage.

The private product directory is `{DIRECTORY}/`. Its catalog contains individual source references
and dated operator statements for {c["site_entries_with_operator"]} site entries. A mineral listed
for a mine is an association. Production statements have separate evidence. A dated issuer
filing updates one historical site observation to the operating rung. The release includes
the earlier assignment, exact source record, reporting period and evidence for the change.
The Step 7 blind result remains 29 of 30. Versioned canonical Parquet tables are under
`data/canonical/{TAG}/`, and QA-cleared publication files are under `data/publish/{TAG}/`.
""".encode()
    outputs[REPORT] = report
    parents, code = required_inputs(root, PRODUCTS)
    return seal(
        root,
        PRODUCTS,
        outputs,
        {"status": "passed", "counts": c},
        parents,
        code,
    )


def verify_supplement(root):
    manifest = load(root, "overrides/pilot_operator_evidence.json")
    path = "pipeline/release_products/production_evidence.py"
    if sha((root / path).read_bytes()) != manifest.get("production_assignment_code_sha256"):
        raise ValueError("Production reconciliation implementation changed")


def check(root, *, local=False):
    verify_supplement(root)
    return verify(root, PRODUCTS, local=local)
