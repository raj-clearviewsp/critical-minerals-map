"""Source-specific normalization retains evidence without canonical inference."""

import hashlib
import io
import json
import struct
import zipfile

import pyarrow as pa
import pytest
from openpyxl import Workbook

from pipeline.normalize import facilities


def snapshot(tmp_path, source, files):
    directory = tmp_path / "data/raw" / source / "2026-10-31"
    directory.mkdir(parents=True)
    for name, content in files.items():
        (directory / name).write_bytes(content)
    (directory / "manifest.json").write_text(json.dumps({"source_id": source}))
    return directory


def workbook(sheet, header, rows, header_row=1, extra=None):
    book = Workbook()
    ws = book.active
    ws.title = sheet
    for _ in range(header_row - 1):
        ws.append([])
    ws.append(header)
    for row in rows:
        ws.append(row)
    for name, content in (extra or {}).items():
        other = book.create_sheet(name)
        for row in content:
            other.append(row)
    output = io.BytesIO()
    book.save(output)
    return output.getvalue()


def consume(root, source):
    result = facilities.parse(root, source)
    rows = list(result["rows"])
    assert sum(t["rows"] for t in result["tables"] if t["role"] == "primary") == (
        len(rows) + len(result["rejects"])
    )
    json.dumps(rows, allow_nan=False)
    return result, rows


def test_gseu_size_thresholds_repeated_deposits_and_explanatory_row(tmp_path):
    headers = [
        "Deposit name",
        "Country",
        "ISO3",
        "Deposit type",
        "CRM",
        "Deposit size",
        "longitude",
        "latitude",
        "Confidential?",
        "Total endowment (remaining + extracted amount of the commodity)",
        "Type of commodity reported",
        "Minimum estimate?",
        None,
    ]
    data = [
        [
            "Same site",
            "France",
            "FRA",
            "laterite",
            "Ni",
            "B",
            2,
            46,
            "yes",
            100,
            "Ni",
            "yes*",
            "closed",
        ],
        [
            "Same site",
            "France",
            "FRA",
            "laterite",
            "Co",
            "C",
            2,
            46,
            None,
            10,
            "Co",
            "yes*",
            "closed",
        ],
        [None] * 11 + ["* Threshold is a minimum estimate"],
    ]
    snapshot(
        tmp_path,
        "gseu_crm",
        {"source.xlsx": workbook("Data", headers, data, 2, {"Deposit sizes": [["Ni", "B", 100]]})},
    )
    result, rows = consume(tmp_path, "gseu_crm")
    assert len(rows) == 2 and rows[0]["native_id"] != rows[1]["native_id"]
    assert rows[0]["row_number"] == 3
    assert rows[0]["raw_size_grade_text"]["Deposit size"] == "B"
    assert rows[0]["raw_size_grade_text"]["Minimum estimate?"] == "yes*"
    assert rows[0]["raw_status"] == {"Deposit mining activity status": "closed"}
    assert rows[0]["location_precision"] is None
    assert result["rejects"][0]["row_number"] == 5
    assert "Threshold" in result["rejects"][0]["raw_fields"]["Minimum estimate?"]
    assert not any(k in rows[0] for k in ["maturity_rung", "site_id"])


def test_ga_ree_preserves_all_duplicate_columns_and_scope_basis(tmp_path):
    headers = [
        "Coastal",
        "Country",
        "Longitude",
        "Latitude",
        "Element",
        "Deposit type",
        "Deposit type",
        "Tonnage (Mt)",
        "Source(s) of data - location",
        None,
    ]
    snapshot(
        tmp_path,
        "ga_global_ree",
        {
            "source.xlsx": workbook(
                "Deposits",
                headers,
                [
                    [
                        "Example",
                        "Australia",
                        120,
                        -25,
                        "North Australian",
                        "Carbonatite",
                        "Supergene",
                        0,
                        "Study (2023)",
                        "unnamed evidence",
                    ]
                ],
                extra={"References": [["Study (2023)"]]},
            )
        },
    )
    result, rows = consume(tmp_path, "ga_global_ree")
    row = rows[0]
    assert row["raw_name"] == "Example"
    assert row["raw_deposit_type"] == {
        "Deposit type [column F]": "Carbonatite",
        "Deposit type [column G]": "Supergene",
    }
    assert row["raw_fields"][" [column J]"] == "unnamed evidence"
    assert row["raw_commodities"] == {"source_scope": "REE"}
    assert row["raw_fields"]["Element"] == "North Australian"
    assert row["raw_size_grade_text"]["Tonnage (Mt)"] == 0
    assert any("documented compilation scope" in note for note in result["notes"])


def test_ga_workbook_no_inferred_country_or_current_operation(tmp_path):
    snapshot(
        tmp_path,
        "ga_critical_2025",
        {
            "source.xlsx": workbook(
                "CM Mines and Deposits 2025",
                ["Name", "State", "Latitude", "Longitude", "Status", "Commodity Group"],
                [["Example", "WA", -25, 120, "Mine - care and maintenance", "REE"]],
                13,
            )
        },
    )
    _, rows = consume(tmp_path, "ga_critical_2025")
    source = next((tmp_path / "data/raw/ga_critical_2025").glob("*/*.xlsx"))
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    assert rows[0]["native_id"] == f"CM Mines and Deposits 2025:sha256:{digest}:row:14"
    assert rows[0]["country"] is None
    assert rows[0]["raw_status"] == {"Status": "Mine - care and maintenance"}
    assert rows[0]["record_kind"] == "facility"


def test_arcgis_layer_identity_and_declared_wgs84_geometry(tmp_path):
    files = {}
    for layer in [4, 7]:
        files[f"layer_{layer}_page_00000.json"] = json.dumps(
            {
                "spatialReference": {"wkid": 4326},
                "features": [
                    {
                        "attributes": {
                            "objectid": 1,
                            "projectname": "Example",
                            "status": "Mineral deposit",
                            "commodities": "Ni",
                            "longitude": 120,
                            "latitude": -25,
                        },
                        "geometry": {"x": 120.0001, "y": -25.0001},
                    }
                ],
            }
        ).encode()
    snapshot(tmp_path, "ga_critical_2025_arcgis", files)
    _, rows = consume(tmp_path, "ga_critical_2025_arcgis")
    assert len({r["native_id"] for r in rows}) == 2
    assert [r["record_kind"] for r in rows] == ["facility", "deposit"]
    assert rows[0]["lon"] == 120.0001 and rows[0]["raw_fields"]["longitude"] == 120
    assert rows[0]["raw_geometry"] == {"x": 120.0001, "y": -25.0001}


def test_icmm_keeps_asset_type_and_unsplit_source_alias(tmp_path):
    snapshot(
        tmp_path,
        "icmm",
        {
            "source.xlsx": workbook(
                "Global Mining Dataset v1.5",
                [
                    "ICMMID",
                    "Mine Name",
                    "Asset Type",
                    "Country",
                    "Latitude",
                    "Longitude",
                    "Primary Commodity",
                    "Secondary Commodity ",
                    "Other Commodities",
                    "Group Names",
                    "Contains",
                ],
                [
                    [
                        "ICMM0001",
                        "Example",
                        "Refinery",
                        "France",
                        46,
                        2,
                        "nickel",
                        "cobalt",
                        None,
                        "First;Second",
                        "WA1;WA2",
                    ]
                ],
            )
        },
    )
    _, rows = consume(tmp_path, "icmm")
    assert rows[0]["native_id"] == "ICMM0001"
    assert rows[0]["raw_fields"]["Asset Type"] == "Refinery"
    assert rows[0]["raw_status"] == {}
    assert rows[0]["aliases"] == ["First;Second"]
    assert rows[0]["raw_commodities"]["Secondary Commodity "] == "cobalt"


def test_wapha_streams_keeps_duplicates_and_invalid_coordinate_evidence(tmp_path):
    files = {}
    for scope, name in [("active", "Mine Name"), ("inactive", "Mine name")]:
        files[f"world_{scope}_mines_17.05.2023.xlsx"] = workbook(
            "Sheet1",
            [name, "Status", "Commodity", "Longitude", "Latitude"],
            [
                ["Repeated", scope.title(), "Ni", 181, 20],
                ["Repeated", scope.title(), "Ni", 181, 20],
            ],
        )
    snapshot(tmp_path, "wapha", files)
    result = facilities.parse(tmp_path, "wapha")
    assert iter(result["rows"]) is result["rows"]
    assert result["tables"] == []  # Workbooks are not eagerly read into memory.
    rows = list(result["rows"])
    assert len(rows) == len({r["native_id"] for r in rows}) == 4
    assert all(r["country"] is None and r["location_precision"] is None for r in rows)
    assert rows[0]["raw_fields"]["Longitude"] == 181
    # The common writer receives original cells so it can report malformed pairs.
    assert rows[0]["lon"] == 181 and rows[0]["lat"] == 20
    assert sum(t["rows"] for t in result["tables"]) == 4


def jasansky_fixture(tmp_path, monkeypatch, orphan=False):
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr("data/facilities.gpkg", b"test fixture")
        archive.writestr(
            "data/commodities.csv",
            "id,facility_id,year,commodity,source_id\n"
            + ("1,MISSING,2020,Me.Ni,det_1\n" if orphan else "1,F.01,2020,Me.Ni,det_1\n"),
        )
        archive.writestr(
            "data/source_ids.csv",
            "source_id,sources,source_urls\ndet_1,Report,https://example.org/report\n",
        )
    snapshot(tmp_path, "jasansky", {"source.zip": output.getvalue()})
    point = struct.pack("<BIdd", 1, 1, 2, 46)
    multipoint = struct.pack("<BII", 1, 4, 1) + point
    rows = [
        dict(
            facility_id="F.01",
            facility_name="Group",
            sub_site_name="Child plant",
            sub_site_other_names="Other plant",
            facility_type="Region",
            country="France",
            primary_commodity="Nickel",
            commodities_products="Nickel, Cobalt",
            activity_status=None,
            activity_status_year=None,
            source_id="det_1",
            geom=multipoint,
        )
    ]
    monkeypatch.setattr(
        facilities.pyogrio,
        "read_arrow",
        lambda *a, **kw: (
            {"crs": "EPSG:4326", "geometry_name": "geom"},
            pa.Table.from_pylist(rows),
        ),
    )
    return point


def test_jasansky_source_groups_subsites_child_rows_and_references(tmp_path, monkeypatch):
    jasansky_fixture(tmp_path, monkeypatch)
    result, rows = consume(tmp_path, "jasansky")
    row = rows[0]
    assert row["native_id"] == "F.01" and row["record_kind"] == "source_group"
    assert row["raw_fields"]["sub_site_name"] == "Child plant"
    assert row["lon"] == 2 and row["lat"] == 46 and row["raw_geometry"]
    child = row["raw_fields"]["_linked_tables"]["data/commodities.csv"][0]
    assert child["raw_fields"]["year"] == "2020"
    assert child["raw_fields"]["source_id"] == "det_1"
    assert child["source_file"].endswith("source.zip") and child["row_number"] == 2
    assert row["reference"]["source_records"][0]["sources"] == "Report"
    assert row["raw_status"]["activity_status"] is None
    assert {t["role"] for t in result["tables"]} == {"primary", "support", "reference"}


def test_jasansky_orphan_support_rows_fail_instead_of_disappearing(tmp_path, monkeypatch):
    jasansky_fixture(tmp_path, monkeypatch, orphan=True)
    with pytest.raises(ValueError, match="Orphan Jasansky"):
        list(facilities.parse(tmp_path, "jasansky")["rows"])


def test_multipoint_has_no_invented_centroid():
    point = struct.pack("<BIdd", 1, 1, 2, 46)
    geometry = struct.pack("<BII", 1, 4, 2) + point + point
    assert facilities._point_wkb(geometry) == (None, None)


def test_missing_required_workbook_field_fails(tmp_path):
    snapshot(
        tmp_path,
        "icmm",
        {"source.xlsx": workbook("Global Mining Dataset v1.5", ["Mine Name"], [["X"]])},
    )
    with pytest.raises(ValueError, match="required columns"):
        list(facilities.parse(tmp_path, "icmm")["rows"])


def test_fallback_ids_bind_file_bytes_and_cache_hash_per_parser(tmp_path, monkeypatch):
    headers = ["Name", "State", "Latitude", "Longitude", "Status", "Commodity Group"]
    records = [
        ["First", "WA", -25, 120, "Mineral deposit", "Ni"],
        ["Second", "WA", -26, 121, "Mineral deposit", "Co"],
    ]
    content = workbook("CM Mines and Deposits 2025", headers, records, 13)
    directory = snapshot(tmp_path, "ga_critical_2025", {"source.xlsx": content})
    path = directory / "source.xlsx"
    original_open = type(path).open
    hash_reads = []

    def tracked_open(self, mode="r", *args, **kwargs):
        if self == path and mode == "rb":
            hash_reads.append(self)
        return original_open(self, mode, *args, **kwargs)

    monkeypatch.setattr(type(path), "open", tracked_open)
    _, first = consume(tmp_path, "ga_critical_2025")
    assert len(hash_reads) == 1  # Two positional IDs share one full-file digest.
    _, unchanged = consume(tmp_path, "ga_critical_2025")
    assert [r["native_id"] for r in unchanged] == [r["native_id"] for r in first]
    path.write_bytes(workbook("CM Mines and Deposits 2025", headers, list(reversed(records)), 13))
    _, reordered = consume(tmp_path, "ga_critical_2025")
    assert reordered[0]["raw_name"] == "Second"
    assert {r["native_id"] for r in reordered}.isdisjoint(r["native_id"] for r in first)
