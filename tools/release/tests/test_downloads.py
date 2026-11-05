"""Round-trip checks on synthetic public rows, including real Tippecanoe when installed."""

import csv
import gzip
import json
import struct
from pathlib import Path

import pyarrow.parquet as pq
import pytest
from shapely import from_wkb

from pipeline.release_products.downloads import TABLES, build_downloads, feature_ids, get_world_tile

ROOT = Path(__file__).resolve().parents[3]
BINARY = ROOT / ".cache/release-tools/tippecanoe-2.79.0/tippecanoe"


def blank():
    return {t: [] for t in TABLES}


def fixture():
    tables = blank()
    tables["source"] = [
        {
            "source_id": "synthetic",
            "name": "Synthetic source",
            "redistribution_ok": True,
            "retrieved_at": "2026-11-04",
            "evidence_grade": "B",
            "checksum": "f" * 64,
            "checksum_basis": "snapshot_manifest",
            "url": "https://example.test/source",
        }
    ]
    tables["site"] = [
        {
            "site_id": "site-1",
            "name": "Cedar",
            "lat": -23.45,
            "lon": 133.7,
            "maturity_rung": 3,
            "commodities": ["Ni", "Co"],
            "country_iso3": "AUS",
            "status": "unknown",
            "review_state": "single_source_provisional",
        }
    ]
    tables["facility"] = [
        {
            "facility_id": "facility-1",
            "site_id": "site-1",
            "name": "Cedar mine",
            "lat": -23.46,
            "lon": 133.71,
            "commodities": ["Ni"],
            "facility_type": "mine",
            "status": "historical",
        }
    ]
    tables["source_record"] = [
        {
            "record_id": "record-1",
            "source_id": "synthetic",
            "name": " \t=SUM(1,2)",
            "lat": -23.45,
            "lon": 133.7,
            "aliases": ["Álias"],
            "evidence": [{"field": "name", "source_url": "https://example.org/record"}],
        },
        {"record_id": "record-2", "source_id": "synthetic", "name": "Unlocated"},
    ]
    tables["country_commodity"] = [
        {
            "country_iso3": "AUS",
            "scope": "country",
            "commodity": "Ni",
            "year": 2025,
            "production": 123.0,
            "production_estimated": True,
            "reserves_raw": ">1000",
            "reserves_lower_bound": 1000,
        }
    ]
    return tables


def tile_meta(path):
    b = path.read_bytes()
    offset, size = struct.unpack_from("<QQ", b, 24)
    return b, json.loads(gzip.decompress(b[offset : offset + size]))


def test_empty_release_uses_typed_empty_tables_and_zero_tile_archive(tmp_path):
    files = build_downloads(blank(), tmp_path, BINARY)
    assert len(files) == 20
    table = pq.read_table(tmp_path / "downloads/site.parquet")
    assert table.num_rows == 0 and str(table.schema.field("maturity_rung").type) == "int64"
    assert json.loads(table.schema.metadata[b"geo"])["columns"]["geometry"]["encoding"] == "WKB"
    b, metadata = tile_meta(tmp_path / "downloads/atlas.pmtiles")
    assert b[:8] == b"PMTiles\x03" and struct.unpack_from("<Q", b, 72)[0] == 0
    assert sorted(v["id"] for v in metadata["vector_layers"]) == ["facilities", "sites"]
    assert (tmp_path / "downloads/data-dictionary.csv").stat().st_size > 1000


@pytest.mark.skipif(not BINARY.is_file(), reason="Pinned real Tippecanoe unavailable")
@pytest.mark.parametrize("facilities", [True, False])
def test_real_tiles_geoparquet_and_deterministic_output(tmp_path, facilities):
    tables = fixture()
    if not facilities:
        tables["facility"] = []
    files = build_downloads(tables, tmp_path / "one", BINARY)
    d = tmp_path / "one/downloads"
    b, meta = tile_meta(d / "atlas.pmtiles")
    assert meta["generator"] == "tippecanoe v2.79.0" and {
        r["id"] for r in meta["vector_layers"]
    } == {"sites", "facilities"}
    assert feature_ids(get_world_tile(b)) == (
        {"sites": ["site-1"], "facilities": ["facility-1"]} if facilities else {"sites": ["site-1"]}
    )
    arr = pq.read_table(d / "source_record.parquet")
    md = json.loads(arr.schema.metadata[b"geo"])["columns"]["geometry"]
    assert md["crs"]["id"]["code"] == "CRS84" and md["bbox"] == [133.7, -23.45, 133.7, -23.45]
    assert sorted(
        [
            None if x is None else (from_wkb(x).x, from_wkb(x).y)
            for x in arr["geometry"].to_pylist()
        ],
        key=str,
    ) == [(133.7, -23.45), None]
    assert pq.read_table(d / "country_commodity.parquet")["production_estimated"].to_pylist() == [
        True
    ]
    with (d / "source_record.csv").open(newline="") as f:
        csv_rows = list(csv.DictReader(f))
    assert next(r for r in csv_rows if r["record_id"] == "record-1")["name"] == "' \t=SUM(1,2)"
    assert (
        next(r for r in arr.to_pylist() if r["record_id"] == "record-1")["name"] == " \t=SUM(1,2)"
    )
    shuffled = {k: list(reversed(v)) for k, v in reversed(list(tables.items()))}
    assert build_downloads(shuffled, tmp_path / "two", BINARY) == files
    times = {f: f.stat().st_mtime_ns for f in d.iterdir()}
    assert build_downloads(tables, tmp_path / "one", BINARY) == files
    assert all(f.stat().st_mtime_ns == ts for f, ts in times.items())


@pytest.mark.parametrize(
    "change",
    [
        {"lat": 91},
        {"lat": None},
        {"location_precision": "country_centroid"},
        {"maturity_rung": True},
        {"private_research": "do not expose"},
        {"evidence": [{"nested": {"reviewer": "private"}}]},
    ],
)
def test_invalid_inputs_fail_before_any_download_is_accepted(tmp_path, change):
    tables = fixture()
    tables["site"][0].update(change)
    with pytest.raises(ValueError):
        build_downloads(tables, tmp_path, BINARY)
    assert not (tmp_path / "downloads").exists()


def test_existing_files_remain_immutable(tmp_path):
    build_downloads(blank(), tmp_path, BINARY)
    p = tmp_path / "downloads/source.csv"
    p.write_text("changed")
    with pytest.raises(ValueError, match="Conflicting"):
        build_downloads(blank(), tmp_path, BINARY)
    assert p.read_text() == "changed"


@pytest.mark.parametrize("change", ["source_hold", "unknown_site", "citation_source"])
def test_distribution_projection_is_closed_over_sources_and_sites(tmp_path, change):
    tables = fixture()
    if change == "source_hold":
        tables["source"][0]["redistribution_ok"] = False
    elif change == "unknown_site":
        tables["facility"][0]["site_id"] = "held-site"
    else:
        tables["site"][0]["evidence"] = [{"source_id": "restricted"}]
    with pytest.raises(ValueError, match="source|site"):
        build_downloads(tables, tmp_path, BINARY)
    assert not (tmp_path / "downloads").exists()
