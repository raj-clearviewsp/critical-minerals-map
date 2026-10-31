"""Synthetic fixtures for source semantics and exhaustive tabular scan guards."""

from __future__ import annotations

import io
import json
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

import pytest
from openpyxl import Workbook

from pipeline.vocab import tabular

SDMX_STRUCTURE = "http://www.sdmx.org/resources/sdmxml/schemas/v2_1/structure"
SDMX_COMMON = "http://www.sdmx.org/resources/sdmxml/schemas/v2_1/common"
XML_LANGUAGE = "{http://www.w3.org/XML/1998/namespace}lang"
AIDDATA_MEMBER = (
    "AidData_CFTM_v2/AidData_Chinese_Financing_for_Transition_Minerals_Dataset_Version_2.0.xlsx"
)


def workbook_bytes(sheets: dict[str, list[list]]) -> bytes:
    book = Workbook()
    book.remove(book.active)
    for name, rows in sheets.items():
        sheet = book.create_sheet(name)
        for row in rows:
            sheet.append(row)
    output = io.BytesIO()
    book.save(output)
    book.close()
    return output.getvalue()


def raw_directory(root: Path, source: str) -> Path:
    directory = root / "data/raw" / source / "2026-10-31"
    directory.mkdir(parents=True)
    return directory


def test_snapshot_selection_ignores_non_date_manifest_folders(tmp_path):
    raw = tmp_path / "data/raw"
    for name in ("2026-10-29", "2026-10-31", "manual", "zzz"):
        folder = raw / "synthetic" / name
        folder.mkdir(parents=True)
        (folder / "manifest.json").write_text("{}")
    assert tabular._snapshot(raw, "synthetic") == raw / "synthetic/2026-10-31"
    for name in ("2026-10-29", "2026-10-31"):
        (raw / "synthetic" / name / "manifest.json").unlink()
    with pytest.raises(ValueError, match="No raw manifest"):
        tabular._snapshot(raw, "synthetic")


def test_required_field_disappearance_fails_instead_of_claiming_empty_coverage(tmp_path):
    audit = tabular.Inventory(tmp_path)
    path = tmp_path / "data/raw/synthetic/table.csv"
    with pytest.raises(ValueError, match="required fields absent.*Commodity"):
        audit.table(
            "synthetic",
            path,
            "records",
            ["Renamed commodity"],
            [["Ni"]],
            {"Commodity": "commodity"},
        )


def test_raw_qualifiers_whitespace_and_duplicate_headers_survive_inventory(tmp_path):
    audit = tabular.Inventory(tmp_path)
    path = tmp_path / "data/raw/synthetic/table.csv"
    audit.table(
        "synthetic",
        path,
        "records",
        ["Commodity", "Deposit type", "Deposit type"],
        [
            ["Ni; Co ", "Carbonatite", "Laterite"],
            ["Ni; Co ", None, "Laterite"],
            ["Ni; Co", None, None],
            [None, None, None],
        ],
        {"Commodity": "commodity", "Deposit type": "deposit_type"},
    )
    result = audit.result()
    values = {(x["field"], x["raw_value"]): x["count"] for x in result["observations"]}
    assert values == {
        ("Commodity", "Ni; Co "): 2,
        ("Commodity", "Ni; Co"): 1,
        ("Deposit type", "Carbonatite"): 1,
        ("Deposit type", "Laterite"): 2,
    }
    table = result["tables"][0]
    assert table["rows"] == 3
    assert [
        (f["column"], f["nonempty_cells"], f["empty_cells"]) for f in table["selected_fields"]
    ] == [("A", 3, 0), ("B", 1, 2), ("C", 2, 1)]


def test_aiddata_financing_progress_is_context_not_mine_status(tmp_path):
    country_fields = [
        "Financier_Country",
        "Country_of_Activity",
        "Country_of_Activity_ISO3",
        "DRA_Country_of_Inc",
        "DRA_Country_of_Inc_ISO3",
        "Overseas_Jurisdiction_ISO3",
    ]
    headers = ["Transition_Minerals", "Commodity", "Status", *country_fields, "Project_Title"]
    countries = ["China", "Canada", "CAN", "Canada", "CAN", ""]
    payload = workbook_bytes(
        {
            "Financial_Contribution": [
                headers,
                ["Copper", "Gold", "Completion", *countries, "Synthetic Nickel company"],
                ["Copper", "Gold", "Cancelled", *countries, "Synthetic REE project"],
            ],
            "Loan_Event": [
                [*headers, "Primary_Minerals", "Subsidiary_Minerals"],
                [
                    "Copper",
                    "Gold",
                    "Completion",
                    *countries,
                    "Synthetic discovery",
                    "Nickel",
                    "Cobalt",
                ],
            ],
            "Org_Role": [["Transition_Minerals", "Host_Country"], ["Copper", "Canada"]],
            "Mineral_Operation_Ownership": [
                ["Host_Country", "Mining_Site"],
                ["Canada", "Synthetic Monazite mine"],
            ],
            "License": [["Not an observation table"]],
        }
    )
    path = raw_directory(tmp_path, "aiddata_transition_v2") / "release.zip"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(AIDDATA_MEMBER, payload)
    before = path.read_bytes()
    audit = tabular.Inventory(tmp_path)
    tabular._aiddata(audit, path)
    result = audit.result()
    assert path.read_bytes() == before
    assert not any(x["kind"] == "status" for x in result["observations"])
    assert {x["raw_value"] for x in result["observations"] if x["kind"] == "commodity"} == {
        "Copper",
        "Gold",
        "Nickel",
        "Cobalt",
    }
    finance_fields = [f for table in result["tables"] for f in table["context_only_fields"]]
    assert len(finance_fields) == 2
    assert {f["kind"] for f in finance_fields} == {"finance_status"}
    assert sum(f["nonempty_cells"] for f in finance_fields) == 3
    prefix = "aiddata_transition_v2: finance-only state inventory (not mine status): "
    state_notes = [
        json.loads(note[len(prefix) :]) for note in result["notes"] if note.startswith(prefix)
    ]
    assert len(state_notes) == 1
    assert {state["raw_value"] for state in state_notes[0]} == {"Completion", "Cancelled"}
    assert sum(state["count"] for state in state_notes[0]) == 3


@pytest.fixture
def oecd_fixture(tmp_path):
    directory = raw_directory(tmp_path, "oecd_export")
    for year in range(2009, 2025):
        content = "COMMODITY,REF_AREA,OBS_STATUS\nHS07_260400,CAN,A\n"
        if year == 2024:
            content += "HS07_284610,CAN,E\n"
        (directory / f"inventory_{year}.csv").write_text(content)
    tree = ET.Element("Structure")
    for name, labels in (
        (
            "CL_PRODUCT_HS2007",
            {
                "HS07_260400": "Nickel Ores And Concentrates",
                "HS07_284610": "Cerium Compounds",
                "HS07_260500": "Cobalt Ores And Concentrates",
            },
        ),
        ("CL_AREA", {"CAN": "Canada"}),
    ):
        codelist = ET.SubElement(tree, f"{{{SDMX_STRUCTURE}}}Codelist", id=name)
        for code, label in labels.items():
            entry = ET.SubElement(codelist, f"{{{SDMX_STRUCTURE}}}Code", id=code)
            ET.SubElement(entry, f"{{{SDMX_COMMON}}}Name", {XML_LANGUAGE: "fr"}).text = "Français"
            ET.SubElement(entry, f"{{{SDMX_COMMON}}}Name", {XML_LANGUAGE: "en"}).text = label
    structure = directory / "structure.xml"
    ET.ElementTree(tree).write(structure, encoding="utf-8", xml_declaration=True)
    return directory, structure


@pytest.mark.parametrize("change", ["missing_year", "unexpected_year"])
def test_oecd_requires_complete_declared_year_span(tmp_path, oecd_fixture, change):
    directory, _ = oecd_fixture
    if change == "missing_year":
        (directory / "inventory_2013.csv").unlink()
    else:
        (directory / "inventory_2025.csv").write_text("COMMODITY,REF_AREA\n")
    with pytest.raises(ValueError, match="every annual CSV from 2009 through 2024"):
        tabular._oecd(tabular.Inventory(tmp_path), directory)


def test_oecd_retains_raw_codes_and_attaches_only_observed_english_labels(tmp_path, oecd_fixture):
    directory, _ = oecd_fixture
    audit = tabular.Inventory(tmp_path)
    tabular._oecd(audit, directory)
    result = audit.result()
    csv_tables = [table for table in result["tables"] if table["table"].endswith(".csv")]
    assert len(csv_tables) == 16
    assert sum(table["rows"] for table in csv_tables) == 17
    assert {x["raw_value"] for x in result["observations"] if x["kind"] == "commodity"} == {
        "HS07_260400",
        "HS07_284610",
    }
    assert {x["field"] for x in result["observations"]} == {"COMMODITY", "REF_AREA"}
    assert {(x["code"], x["label"]) for x in result["code_labels"]} == {
        ("HS07_260400", "Nickel Ores And Concentrates"),
        ("HS07_284610", "Cerium Compounds"),
        ("CAN", "Canada"),
    }
    products = next(t for t in result["tables"] if t["table"] == "CL_PRODUCT_HS2007")
    assert products["rows"] == 3
    assert products["observed_codes"] == 2


@pytest.mark.parametrize("change", ["missing_code", "no_english_name", "missing_codelist"])
def test_oecd_rejects_observed_codes_without_publisher_english_label(
    tmp_path, oecd_fixture, change
):
    directory, structure = oecd_fixture
    tree = ET.parse(structure)
    codelist = tree.find(f".//{{{SDMX_STRUCTURE}}}Codelist[@id='CL_PRODUCT_HS2007']")
    code = codelist.find(f"{{{SDMX_STRUCTURE}}}Code[@id='HS07_284610']")
    if change == "missing_code":
        codelist.remove(code)
    elif change == "no_english_name":
        for name in list(code):
            if name.get(XML_LANGUAGE) == "en":
                code.remove(name)
    else:
        tree.getroot().remove(codelist)
    tree.write(structure, encoding="utf-8", xml_declaration=True)
    with pytest.raises(ValueError, match="CL_PRODUCT_HS2007"):
        tabular._oecd(tabular.Inventory(tmp_path), directory)


def test_ga_element_and_assay_headers_do_not_create_commodity_observations(tmp_path, monkeypatch):
    # Exercise extract()'s actual source policy with one real synthetic workbook;
    # other sources have no observations in this focused fixture.
    for source in tabular.SOURCE_IDS:
        directory = raw_directory(tmp_path, source)
        (directory / "manifest.json").write_text("{}")
        suffix = ".zip" if source in {"jasansky", "aiddata_transition_v2"} else ".xlsx"
        if source == "usgs_mcs_2026":
            (directory / "fixture.csv").write_text("Commodity,Country\n")
        else:
            (directory / f"fixture{suffix}").write_bytes(b"")
    ga_path = tmp_path / "data/raw/ga_global_ree/2026-10-31/fixture.xlsx"
    ga_path.write_bytes(
        workbook_bytes(
            {
                "Deposits": [
                    [
                        "Country",
                        "Element",
                        "Deposit type",
                        "Deposit type",
                        "Deposit type",
                        "Nd2O3 (%)",
                        "CRIRSCO-compliant resources or production- REEs",
                        "Comments",
                    ],
                    [
                        "Australia",
                        "North Australian",
                        "Carbonatite",
                        "Laterite",
                        "Placer",
                        0.5,
                        "P",
                        "Synthetic Nickel project description",
                    ],
                ],
            }
        )
    )
    original = tabular.Inventory.workbook

    def one_workbook(self, source, path, spec, content=None, member=None):
        if source == "ga_global_ree":
            return original(self, source, path, spec, content, member)
        return None

    monkeypatch.setattr(tabular.Inventory, "workbook", one_workbook)
    for helper in ("_ga_arcgis", "_jasansky", "_oecd", "_aiddata"):
        monkeypatch.setattr(tabular, helper, lambda *_: None)
    result = tabular.extract(tmp_path)
    ga_values = [x for x in result["observations"] if x["source_id"] == "ga_global_ree"]
    assert {x["kind"] for x in ga_values} == {"country", "deposit_type"}
    assert {x["raw_value"] for x in ga_values if x["kind"] == "deposit_type"} == {
        "Carbonatite",
        "Laterite",
        "Placer",
    }
    ga_table = next(t for t in result["tables"] if t["source_id"] == "ga_global_ree")
    assert "Element" in ga_table["fields"]
    assert "Nd2O3 (%)" in ga_table["fields"]
    assert any("no commodity-code column" in note for note in result["notes"])
