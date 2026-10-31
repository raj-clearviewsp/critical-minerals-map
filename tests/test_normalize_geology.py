"""Geological adapters preserve source records, context and table reconciliation."""

import csv
import hashlib
import io
import json
import zipfile

import pyarrow as pa
import pytest

from pipeline.normalize import geology


def snapshot(root, source, files):
    directory = root / "data/raw" / source / "2026-10-31"
    directory.mkdir(parents=True)
    for name, content in files.items():
        (directory / name).write_bytes(content)
    (directory / "manifest.json").write_text(
        json.dumps({"files": [{"path": name} for name in files]})
    )


def archive_files(files):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, content in files.items():
            archive.writestr(name, content)
    return buffer.getvalue()


def mock_gdb(monkeypatch, layers):
    monkeypatch.setattr(
        geology.pyogrio,
        "list_layers",
        lambda _: [(name, geometry) for name, (geometry, _) in layers.items()],
    )
    monkeypatch.setattr(
        geology.pyogrio,
        "read_info",
        lambda _, layer, **kwargs: {
            "features": len(layers[layer][1]),
            "fields": [key for key in layers[layer][1][0] if key != "Shape"],
        },
    )

    def read_arrow(_, layer, **kwargs):
        geometry, rows = layers[layer]
        return (
            {
                "crs": "EPSG:4326" if geometry else None,
                "fid_column": "OBJECTID",
                "geometry_name": "Shape" if geometry else "",
            },
            pa.Table.from_pylist(rows),
        )

    monkeypatch.setattr(geology.pyogrio, "read_arrow", read_arrow)


def ree_row(oid, **changes):
    row = {
        "OBJECTID": oid,
        "ID_No": 3352.0,
        "Name": "Example deposit",
        "Name_Other": "Two, names remain a raw alias cell",
        "Latitude": 20.0,
        "Longitude": 30.0,
        "Country": "Country label ",
        "Commods": "REE(?), Ni",
        "Dep_Type": "carbonatite(?)",
        "Dep_Note": "source note",
        "Dep_Form": "lens",
        "Rec_Type": "district",
        "Status": "Occurrence(?)",
        "Stat_Note": "unconfirmed",
        "P_Status": "No production(?)",
        "PStat_Note": "historical",
        "REE": "0.2% TREO",
        "Ref_List": "Test (2020); Unmatched (1990)",
        "Shape": b"original-wkb",
    }
    row.update(changes)
    return row


def test_ree_business_id_duplicates_and_nonspatial_rows_are_preserved(tmp_path, monkeypatch):
    snapshot(tmp_path, "usgs_global_ree", {"ree.zip": archive_files({"test.gdb/": b""})})
    unlocated = ree_row(1)
    del unlocated["Shape"]
    mock_gdb(
        monkeypatch,
        {
            "Global_REE": ("Point", [ree_row(1), ree_row(2)]),
            "Global_REE_nonspatial_table": (None, [unlocated]),
            "All_Global_REE_references": (
                None,
                [{"OBJECTID": 1, "short_ref": "Test (2020)", "full_ref": "Complete citation"}],
            ),
        },
    )
    result = geology.parse(tmp_path, "usgs_global_ree")
    rows = list(result["rows"])
    assert len(rows) == 3
    assert len({row["native_id"] for row in rows}) == 3
    assert all(row["raw_fields"]["ID_No"] == 3352 for row in rows)
    assert rows[0]["raw_geometry"] == b"original-wkb".hex()
    assert rows[0]["aliases"] == ["Two, names remain a raw alias cell"]
    assert rows[0]["raw_commodities"] == {"Commods": "REE(?), Ni"}
    assert rows[0]["reference"]["linked_reference_rows"][0]["full_ref"] == "Complete citation"
    assert rows[0]["reference"]["unmatched_citation_text"] == ["Unmatched (1990)"]
    off_map = next(row for row in rows if "nonspatial" in row["source_table"])
    assert off_map["lat"] is off_map["lon"] is None
    assert off_map["location_precision"] == "none"
    assert off_map["raw_fields"]["Latitude"] == 20.0
    profile = next(table for table in result["tables"] if table["table"] == "Global_REE")
    assert profile["duplicate_business_ids"] == [{"ID_No": 3352.0, "rows": 2}]


def test_true_duplicate_gdb_native_ids_raise(tmp_path, monkeypatch):
    snapshot(tmp_path, "usgs_global_ree", {"ree.zip": archive_files({"test.gdb/": b""})})
    mock_gdb(
        monkeypatch,
        {
            "Global_REE": ("Point", [ree_row(1), ree_row(1)]),
            "Global_REE_nonspatial_table": (None, [ree_row(1)]),
        },
    )
    with pytest.raises(ValueError, match="Duplicate source native ID"):
        list(geology.parse(tmp_path, "usgs_global_ree")["rows"])


def test_polygon_geometry_survives_without_centroid_inference(tmp_path, monkeypatch):
    snapshot(tmp_path, "usgs_global_critical", {"global.zip": archive_files({"test.gdb/": b""})})
    row = {
        "OBJECTID": 1,
        "DEPOSIT_NAME": "Source district",
        "CRITICAL_MINERAL": "Cobalt",
        "DEPOSIT_TYPE": "Source type",
        "LATITUDE": None,
        "LONGITUDE": None,
        "LOCATION": "Unresolved region",
        "Shape": b"polygon-wkb",
    }
    mock_gdb(
        monkeypatch,
        {
            "PP1802_CritMin_polys": ("MultiPolygon", [row]),
            "PP1802_CritMin_pts": ("Point", [{**row, "LATITUDE": 2.0, "LONGITUDE": 3.0}]),
        },
    )
    rows = list(geology.parse(tmp_path, "usgs_global_critical")["rows"])
    polygon = next(row for row in rows if "polys" in row["source_table"])
    assert polygon["raw_geometry"] == b"polygon-wkb".hex()
    assert polygon["lat"] is polygon["lon"] is None
    assert polygon["raw_status"] == {}
    assert len(rows) == 2


def test_us_ranking_and_raw_geometry_are_preserved(tmp_path):
    attributes = {
        "fid": 11,
        "deposit_na": "Source name",
        "lat_wgs84": 12,
        "long_wgs84": 13,
        "critmin": "cobalt, nickel",
        "deptype": "source deposit type",
        "minsystem": "source mineral system",
        "depcat": "CM-past producer+resources",
        "rank": 4,
        "resources1": "Estimate source text",
        "prduction1": "Historic production text",
        "references_": "Reference",
        "links": "https://example.test/record",
        "state": "PR",
    }
    page = {
        "objectIdFieldName": "fid",
        "features": [{"attributes": attributes, "geometry": {"x": 13, "y": 12}}],
    }
    snapshot(
        tmp_path,
        "usgs_us_critical",
        {"page_00000.json": json.dumps(page).encode(), "object_ids.json": b'{"objectIds":[11]}'},
    )
    row = next(geology.parse(tmp_path, "usgs_us_critical")["rows"])
    assert row["raw_fields"]["rank"] == row["raw_size_grade_text"]["rank"] == 4
    assert row["country"] is None
    assert row["raw_geometry"] == {"type": "Point", "coordinates": [13, 12]}
    assert row["raw_fields"]["source_geometry"] == {"x": 13, "y": 12}


def test_laterite_numeric_evidence_is_not_turned_into_production(tmp_path):
    row = {
        "NameDeposit": "Example",
        "OtherNames": "Alias, unsplit",
        "LatitudeDecimal": "n.d.",
        "LongitudeDecimal": "n.d.",
        "Country": "Côte d’Ivoire",
        "Subtype": "(III) oxide, Mg silicate",
        "Status": "mining operations planned",
        "StartupDate": "2020",
        "Ni grade": "≤1.2",
        "Co grade": "0",
        "References": "########",
        "Source TonnGrade": "Historic report",
        "ResourceTonnage": "35.6",
    }
    out = io.StringIO(newline="")
    writer = csv.DictWriter(out, fieldnames=list(row), lineterminator="\r")
    writer.writeheader()
    writer.writerow(row)
    snapshot(tmp_path, "usgs_laterites", {"laterites.csv": out.getvalue().encode("mac_roman")})
    result = next(geology.parse(tmp_path, "usgs_laterites")["rows"])
    assert result["raw_commodities"]["Co grade"] == "0"
    assert result["raw_size_grade_text"]["Ni grade"] == "≤1.2"
    assert result["raw_status"]["Status"] == "mining operations planned"
    assert result["reference"]["References"] == "########"
    assert result["lat"] == result["lon"] == "n.d."  # Common writer validates the raw cells.


def test_laterite_fallback_ids_are_bound_to_source_bytes(tmp_path):
    fields = [
        "NameDeposit",
        "LatitudeDecimal",
        "LongitudeDecimal",
        "Country",
        "Subtype",
        "Status",
        "StartupDate",
        "References",
        "Source TonnGrade",
    ]

    def content(names):
        out = io.StringIO(newline="")
        writer = csv.DictWriter(out, fieldnames=fields, lineterminator="\r")
        writer.writeheader()
        for name in names:
            writer.writerow({**dict.fromkeys(fields, ""), "NameDeposit": name})
        return out.getvalue().encode("mac_roman")

    original = content(["First deposit", "Second deposit"])
    snapshot(tmp_path, "usgs_laterites", {"laterites.csv": original})
    first = geology.parse(tmp_path, "usgs_laterites")
    first_ids = [row["native_id"] for row in first["rows"]]
    digest = hashlib.sha256(original).hexdigest()
    assert first_ids == [f"laterites:{digest}:1", f"laterites:{digest}:2"]
    assert first["tables"][0]["source_sha256"] == digest
    assert [row["native_id"] for row in geology.parse(tmp_path, "usgs_laterites")["rows"]] == (
        first_ids
    )

    path = tmp_path / "data/raw/usgs_laterites/2026-10-31/laterites.csv"
    path.write_bytes(content(["Second deposit", "First deposit"]))
    reordered_ids = [row["native_id"] for row in geology.parse(tmp_path, "usgs_laterites")["rows"]]
    assert set(first_ids).isdisjoint(reordered_ids)


def mrds_fixture(tmp_path, main):
    files = {
        "MRDS.txt": "dep_id\tname\tdev_stat\turl\tcode_list\tlongitude\tlatitude\n" + main,
        "Names.txt": "dep_id\tname\tstatus\n1\tOld name\tPrevious\n999\tOrphan\tPrevious\n",
        "Commodity.txt": 'dep_id\tcode\tcommod\n1\tNI_R\t"Nickel, Refiner"\n',
        "Materials.txt": "dep_id\tore_gangue\tmaterial\n1\tOre\tNickeline\n",
        "Place.txt": "dep_id\tfips\n1\tf06071\n2\tfCA\n999\tfCN\n",
        "Dups.txt": "dep_id\tdep_id_dup\trel_type\n1\t2\tRelated record\n",
        "Accession.txt": "onlink\twhen\nhttps://example.test/archive\t2019-03-06 12:05\n",
        "About.txt": "table\tfield\tdescription\nMRDS\tdep_id\tRecord identifier\n",
        "mrds.met": "Original metadata",
        "schema.ini": "Original field metadata",
    }
    snapshot(tmp_path, "usgs_mrds", {"mrds.zip": archive_files(files)})


def test_mrds_join_and_orphan_accounting_do_not_create_extra_deposits(tmp_path):
    mrds_fixture(
        tmp_path,
        "1\tPrimary\tPlant\thttps://example.test/one\t NI_R \t1\t2\n"
        "2\tSecond\tPast Producer\thttps://example.test/two\t CO \t3\t4\n",
    )
    result = geology.parse(tmp_path, "usgs_mrds")
    rows = list(result["rows"])
    assert len(rows) == 2
    assert rows[0]["record_kind"] == "facility"
    assert rows[1]["record_kind"] == "deposit"
    assert rows[0]["aliases"] == ["Old name"]
    child = rows[0]["raw_fields"]["linked_tables"]["Commodity.txt"][0]
    assert child["row_number"] == 1
    assert child["values"]["commod"] == "Nickel, Refiner"
    assert rows[0]["raw_fields"]["linked_tables"]["Dups.txt"][0]["values"]["dep_id_dup"] == "2"
    place = next(t for t in result["tables"] if t["table"] == "Place.txt")
    assert place["rows"] == 3
    assert place["linked_rows"] == 2
    assert place["unmatched_parent_rows"] == 1
    assert all(row["country"] is None for row in rows)
    assert result["rejects"] == []


def test_duplicate_mrds_id_fails_before_records_are_emitted(tmp_path):
    mrds_fixture(
        tmp_path,
        "1\tFirst\tOccurrence\thttps://example.test/one\t CO \t1\t2\n"
        "1\tDuplicate\tOccurrence\thttps://example.test/one\t CO \t1\t2\n",
    )
    with pytest.raises(ValueError, match="Duplicate source native ID"):
        geology.parse(tmp_path, "usgs_mrds")
