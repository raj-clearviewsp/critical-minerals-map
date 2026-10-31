"""Source formats retain contextual terms and keep nonspatial evidence visible."""

import csv
import io
import json
import zipfile
from pathlib import Path

import pyarrow as pa
import pytest

from pipeline.vocab import geology


def snapshot(root, source, files):
    directory = root / "data/raw" / source / "2026-10-31"
    directory.mkdir(parents=True)
    for name, content in files.items():
        (directory / name).write_bytes(content)
    (directory / "manifest.json").write_text(
        json.dumps({"files": [{"path": name} for name in files]})
    )


def test_laterite_macroman_embedded_cr_and_header_evidence(tmp_path):
    out = io.StringIO(newline="")
    fields = [*geology.LATERITE_FIELDS, "NameDeposit", "Ni grade", "Co grade"]
    writer = csv.DictWriter(out, fieldnames=fields, lineterminator="\r")
    writer.writeheader()
    writer.writerow(
        {
            "Status": "exploration,\rfeasibility study",
            "Subtype": "(III) oxide, Mg silicate ",
            "Country": "Côte d’Ivoire (Ivory Coast)",
            "CountryCode": "IVCO",
            "NameDeposit": "Example",
            "Ni grade": "≤1",
            "Co grade": "0",
        }
    )
    snapshot(tmp_path, "usgs_laterites", {"laterites.csv": out.getvalue().encode("mac_roman")})
    result = geology.extract(tmp_path)
    observations = result["observations"]
    assert next(o for o in observations if o["field"] == "Status")["raw_value"] == (
        "exploration,\rfeasibility study"
    )
    assert next(o for o in observations if o["field"] == "Subtype")["raw_value"].endswith(" ")
    assert next(o for o in observations if o["field"] == "Country")["raw_value"].startswith(
        "Côte d’Ivoire"
    )
    commodities = [o for o in observations if o["kind"] == "commodity"]
    assert {o["raw_value"] for o in commodities} == {"Ni grade", "Co grade"}
    assert all(o["value_origin"] == "column_header" and o["count"] == 1 for o in commodities)
    assert result["tables"][0]["rows"] == 1


def test_mrds_codes_name_modifiers_and_name_history_have_distinct_meanings(tmp_path):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(
            "MRDS.txt", 'dep_id\tcode_list\tdev_stat\n1\t" NI_R CO "\t"Past Producer"\n'
        )
        archive.writestr("Commodity.txt", 'dep_id\tcode\tcommod\n1\tNI_R\t"Nickel, Refiner"\n')
        archive.writestr("Names.txt", 'dep_id\tname\tstatus\n1\t"Old Name"\t"Previous"\n')
        archive.writestr("Place.txt", "dep_id\tfips\n1\tf06071\n")
    snapshot(tmp_path, "usgs_mrds", {"mrds.zip": buffer.getvalue()})
    result = geology.extract(tmp_path)
    values = {(o["field"], o["raw_value"]) for o in result["observations"]}
    assert ("code_list", " NI_R CO ") in values
    assert ("commod", "Nickel, Refiner") in values
    assert ("dev_stat", "Past Producer") in values
    assert ("status", "Previous") not in values
    assert ("fips", "f06071") in values
    assert result["lookups"][0]["rows"] == [
        {"code": "NI_R", "commod": "Nickel, Refiner", "count": 1}
    ]


def test_geodatabase_audit_includes_nonspatial_and_preserves_uncertainty(tmp_path, monkeypatch):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("example.gdb/", b"")
    snapshot(tmp_path, "usgs_global_ree", {"ree.zip": buffer.getvalue()})
    layers = {"Global_REE": "Point", "Global_REE_nonspatial_table": None}
    tables = {
        layer: pa.Table.from_pylist(
            [
                {
                    "ID_No": index,
                    "Commods": "REE(?), Ni",
                    "Dep_Type": "carbonatite(?)",
                    "Status": "Deposit(?)",
                    "P_Status": "No production(?)",
                    "Country": "China ",
                }
            ]
        )
        for index, layer in enumerate(layers, start=1)
    }
    monkeypatch.setattr(geology.pyogrio, "list_layers", lambda _: list(layers.items()))
    monkeypatch.setattr(
        geology.pyogrio,
        "read_info",
        lambda _, layer, **kwargs: {"features": 1, "fields": tables[layer].column_names},
    )

    def read_arrow(_, layer, read_geometry):
        assert read_geometry is False
        return {}, tables[layer]

    monkeypatch.setattr(geology.pyogrio, "read_arrow", read_arrow)
    result = geology.extract(tmp_path)
    assert {o["table"] for o in result["observations"]} == set(layers)
    assert sum(t["rows"] for t in result["tables"]) == 2
    assert {o["raw_value"] for o in result["observations"] if o["kind"] == "commodity"} == {
        "REE(?), Ni"
    }
    assert all(o["row_examples"][0]["id_field"] == "ID_No" for o in result["observations"])


def test_archive_traversal_is_rejected_before_extraction(tmp_path):
    archive = tmp_path / "unsafe.zip"
    with zipfile.ZipFile(archive, "w") as value:
        value.writestr("../unexpected", b"unsafe")
    with pytest.raises(ValueError, match="Unsafe archive"):
        geology._extract_archive(archive, tmp_path / "scratch")
    assert not (tmp_path / "unexpected").exists()


def test_missing_expected_source_column_does_not_silently_drop_evidence(tmp_path):
    audit = geology._Audit(Path(tmp_path))
    source = tmp_path / "data/raw/example/2026-10-31/rows.csv"
    with pytest.raises(ValueError, match="missing vocabulary fields"):
        audit.table("example", source, "rows", [{"other": "Ni"}], ["other"], {"code": "commodity"})
