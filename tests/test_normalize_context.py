"""Small source-shaped fixtures for context staging, without real source records."""

from __future__ import annotations

import csv
import io
import xml.etree.ElementTree as ET
import zipfile
from collections import Counter
from datetime import datetime

import pytest
from openpyxl import Workbook

from pipeline.normalize.context import AIDDATA_MEMBER, MCS_HEADERS, OECD_DIMENSIONS, parse


def directory(root, source):
    path = root / "data/raw" / source / "2026-10-31"
    path.mkdir(parents=True)
    (path / "manifest.json").write_text("{}")
    return path


def write_csv(path, headers, rows, encoding="utf-8"):
    with path.open("w", encoding=encoding, newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(headers)
        writer.writerows(rows)


def reconcile(parsed, rows):
    accepted = Counter((r["source_file"], r["source_table"]) for r in rows)
    rejected = Counter((r["source_file"], r["source_table"]) for r in parsed["rejects"])
    for profile in parsed["tables"]:
        if profile["role"] == "primary":
            key = (profile["file"], profile["table"])
            assert profile["rows"] == accepted[key] + rejected[key]
    assert all(reject["reason"] for reject in parsed["rejects"])


def mcs_row(**changes):
    row = dict.fromkeys(MCS_HEADERS, "")
    row.update(
        {
            "MCS chapter": "SYNTHETIC",
            "Section": "World statistics—source text",
            "Commodity": "Nickel",
            "Country": "World total",
            "Statistics": "Production",
            "Statistics_detail": "Unconverted source statistic",
            "Unit": "metric tons",
            "Year": "2025",
            "Value": "10,000",
            "Notes": "e Estimate; not a measured zero.",
            "Is critical mineral 2025": "Yes",
            "Other notes": "Source note",
        }
    )
    row.update(changes)
    return [row[key] for key in MCS_HEADERS]


def test_mcs_keeps_statistics_footnotes_duplicates_and_order_independent_ids(tmp_path):
    path = directory(tmp_path, "usgs_mcs_2026") / "MCS2026_Commodities_Data.csv"
    source_rows = [mcs_row(), mcs_row(Notes="Different qualifier"), mcs_row()]
    write_csv(path, MCS_HEADERS, source_rows, "cp1252")
    before = path.read_bytes()
    parsed = parse(tmp_path, "usgs_mcs_2026")
    assert parsed["tables"] == []  # Input records are consumed lazily.
    rows = list(parsed["rows"])
    assert path.read_bytes() == before
    assert len(rows) == 3
    assert len({row["native_id"] for row in rows}) == 3
    assert {row["country"] for row in rows} == {"World total"}
    assert all(row["raw_fields"]["Value"] == "10,000" for row in rows)
    assert all(row["raw_fields"]["Year"] == "2025" for row in rows)
    assert rows[0]["raw_size_grade_text"]["Unit"] == "metric tons"
    assert rows[0]["reference"]["Notes"] == "e Estimate; not a measured zero."
    assert rows[0]["raw_fields"]["Section"] == "World statistics—source text"
    assert all(row["lat"] is None and row["lon"] is None for row in rows)
    assert {row["location_precision"] for row in rows} == {"none"}
    reconcile(parsed, rows)
    write_csv(path, MCS_HEADERS, list(reversed(source_rows)), "cp1252")
    rerun = list(parse(tmp_path, "usgs_mcs_2026")["rows"])
    assert {row["native_id"] for row in rerun} == {row["native_id"] for row in rows}


def test_malformed_mcs_row_has_complete_reject_evidence(tmp_path):
    path = directory(tmp_path, "usgs_mcs_2026") / "MCS2026_Commodities_Data.csv"
    write_csv(path, MCS_HEADERS, [mcs_row(), ["too", "few", "columns"]], "cp1252")
    parsed = parse(tmp_path, "usgs_mcs_2026")
    rows = list(parsed["rows"])
    assert len(rows) == 1
    assert len(parsed["rejects"]) == 1
    reject = parsed["rejects"][0]
    assert reject["row_number"] == 3
    assert reject["raw_fields"]["__cells__"] == ["too", "few", "columns"]
    assert "column count" in reject["reason"]
    reconcile(parsed, rows)


def test_mcs_schema_drift_is_not_silently_accepted(tmp_path):
    path = directory(tmp_path, "usgs_mcs_2026") / "MCS2026_Commodities_Data.csv"
    write_csv(path, ["Commodity", "Country"], [["Nickel", "Canada"]])
    with pytest.raises(ValueError, match="missing required columns"):
        list(parse(tmp_path, "usgs_mcs_2026")["rows"])


def oecd_fixture(root):
    path = directory(root, "oecd_export")
    headers = [*OECD_DIMENSIONS, "OBS_VALUE", "OBS_STATUS", "UNIT_MULT"]
    for year in range(2009, 2025):
        row = ["FLOW(3.0)", "CAN", "HS07_260400", "M6", "A1", "TEMP", str(year), "no", "E", "3"]
        write_csv(path / f"inventory_{year}.csv", headers, [row])
    tree = ET.Element("Structure")
    namespace = "http://www.sdmx.org/resources/sdmxml/schemas/v2_1/structure"
    for name, codes in (
        ("CL_AREA", ["CAN"]),
        ("CL_PRODUCT_HS2007", ["HS07_260400", "HS07_284610"]),
    ):
        codelist = ET.SubElement(tree, f"{{{namespace}}}Codelist", id=name)
        for code in codes:
            ET.SubElement(codelist, f"{{{namespace}}}Code", id=code)
    ET.ElementTree(tree).write(path / "structure.xml", encoding="utf-8")
    return path, headers


def test_oecd_streams_all_years_and_retains_negative_values_and_attributes(tmp_path):
    oecd_fixture(tmp_path)
    parsed = parse(tmp_path, "oecd_export")
    assert iter(parsed["rows"]) is parsed["rows"]
    assert parsed["tables"] == []
    first = next(parsed["rows"])
    assert next(p for p in parsed["tables"] if p["role"] == "primary")["rows"] == 1
    rows = [first, *parsed["rows"]]
    assert len(rows) == 16
    assert len({row["native_id"] for row in rows}) == 16
    assert {row["raw_fields"]["TIME_PERIOD"] for row in rows} == {str(y) for y in range(2009, 2025)}
    assert all(row["raw_fields"]["OBS_VALUE"] == "no" for row in rows)
    assert all(row["raw_fields"]["OBS_STATUS"] == "E" for row in rows)
    assert all(row["raw_fields"]["UNIT_MULT"] == "3" for row in rows)
    assert all(row["raw_status"] == {} and row["raw_size_grade_text"] == {} for row in rows)
    assert all(row["raw_commodities"] == {"COMMODITY": "HS07_260400"} for row in rows)
    assert {(p["table"], p["rows"]) for p in parsed["tables"] if p["role"] == "reference"} == {
        ("CL_AREA", 1),
        ("CL_PRODUCT_HS2007", 2),
    }
    reconcile(parsed, rows)


def test_oecd_natural_id_uses_dimensions_not_measurement_values(tmp_path):
    path, headers = oecd_fixture(tmp_path)
    original = list(parse(tmp_path, "oecd_export")["rows"])[0]
    base = original["raw_fields"]
    revised = {**base, "OBS_VALUE": "yes", "OBS_STATUS": "", "UNIT_MULT": "0"}
    distinct = {**base, "POLICY_DESCRIPTION": "DIFFERENT"}
    write_csv(
        path / "inventory_2009.csv",
        headers,
        [[row[h] for h in headers] for row in [revised, distinct]],
    )
    changed = list(parse(tmp_path, "oecd_export")["rows"])
    assert changed[0]["native_id"] == original["native_id"]
    assert changed[1]["native_id"] != original["native_id"]
    assert changed[0]["raw_fields"]["OBS_VALUE"] == "yes"


@pytest.mark.parametrize("change", ["missing_year", "duplicate_year"])
def test_oecd_rejects_incomplete_or_duplicate_annual_partitions(tmp_path, change):
    path, headers = oecd_fixture(tmp_path)
    if change == "missing_year":
        (path / "inventory_2012.csv").unlink()
    else:
        write_csv(path / "duplicate_2012.csv", headers, [])
    with pytest.raises(ValueError, match="exactly one annual CSV"):
        list(parse(tmp_path, "oecd_export")["rows"])


@pytest.mark.parametrize("change", ["wrong_year", "missing_dimension"])
def test_oecd_bad_natural_key_is_rejected_with_original_fields(tmp_path, change):
    path, headers = oecd_fixture(tmp_path)
    row = ["FLOW(3.0)", "CAN", "HS07_260400", "M6", "A1", "TEMP", "2009", "no", "", ""]
    if change == "wrong_year":
        row[headers.index("TIME_PERIOD")] = "2010"
    else:
        row[headers.index("POLICY_VARIATION")] = ""
    write_csv(path / "inventory_2009.csv", headers, [row])
    parsed = parse(tmp_path, "oecd_export")
    rows = list(parsed["rows"])
    assert len(rows) == 15
    assert len(parsed["rejects"]) == 1
    assert parsed["rejects"][0]["raw_fields"] == dict(zip(headers, row, strict=True))
    reconcile(parsed, rows)


def aiddata_fixture(root):
    path = directory(root, "aiddata_transition_v2") / "release.zip"
    financial = [
        [
            "AidData_Record_ID",
            "Title",
            "Country_of_Activity",
            "Transition_Minerals",
            "Status",
            "Amount_Original_Currency",
            "Original_Currency",
            "Loan_Event_ID",
            "Loan_Event_Tranche",
            "Mining_Site_ID",
            "All_Source_URLs",
        ],
        [
            1,
            "Synthetic finance A",
            "Canada",
            "Nickel|Cobalt",
            "Completion",
            1000,
            "CAD",
            42,
            "A",
            7,
            "https://example.test/document",
        ],
        [
            2,
            "Synthetic finance B",
            "Canada",
            "Copper",
            "Implementation",
            2000,
            "USD",
            42,
            "A",
            7,
            None,
        ],
    ]
    loan = [
        [
            "Loan_Event_ID",
            "Loan_Event_Tranche",
            "Title",
            "Country_of_Activity",
            "Transition_Minerals",
            "Primary_Minerals",
            "Subsidiary_Minerals",
            "Commitment_Date",
        ],
        [42, "A", "Synthetic loan", "Canada", "Nickel", "Nickel", "Cobalt", datetime(2020, 1, 2)],
        [42, "B", "Synthetic loan", "Canada", "Nickel", "Nickel", "Cobalt", None],
    ]
    relationship = [42, "Synthetic Bank", "Canada", "Nickel"]
    ownership = [
        [
            "Investors_Ownership_ID",
            "Mining_Site",
            "Host_Country",
            "Mining_Site_ID",
            "Equity_Holder",
            "Equity_Percentage",
            "Loan_Event_IDs",
        ],
        [7, "Synthetic asset", "Canada", 7, "Synthetic shareholder", 0.51, "42|43"],
        [7, "Synthetic asset", "Canada", 7, "Synthetic shareholder", 0.49, "42|43"],
    ]
    sheets = {
        "Contents": [["Synthetic title"], [], ["Table descriptions"]],
        "Financial_Contribution": financial,
        "Loan_Event": loan,
        "Org_Role": [
            ["Loan_Event_ID", "Organization_Name", "Host_Country", "Transition_Minerals"],
            relationship,
            relationship,
        ],
        "Mineral_Operation_Ownership": ownership,
        "License": [["Synthetic reference"], ["Not a data record"]],
    }
    book = Workbook()
    book.remove(book.active)
    for name, source_rows in sheets.items():
        sheet = book.create_sheet(name)
        for row in source_rows:
            sheet.append(row)
    output = io.BytesIO()
    book.save(output)
    book.close()
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(AIDDATA_MEMBER, output.getvalue())
    return path


def test_aiddata_keeps_financial_native_ids_relations_and_duplicate_ownership_evidence(tmp_path):
    path = aiddata_fixture(tmp_path)
    before = path.read_bytes()
    parsed = parse(tmp_path, "aiddata_transition_v2")
    rows = list(parsed["rows"])
    assert path.read_bytes() == before
    assert len(rows) == len({row["native_id"] for row in rows}) == 8
    assert Counter(row["record_kind"] for row in rows) == {"finance": 6, "finance_asset": 2}
    assert all(row["raw_status"] == {} and row["raw_deposit_type"] == {} for row in rows)
    assert all(row["raw_size_grade_text"] == {} for row in rows)
    assert all(row["lat"] is None and row["lon"] is None for row in rows)
    financial = [row for row in rows if row["source_table"].endswith("!Financial_Contribution")]
    assert len(financial) == 2  # Shared loan/site references do not collapse source records.
    assert financial[0]["raw_fields"]["Status"] == "Completion"
    assert financial[0]["raw_fields"]["Original_Currency"] == "CAD"
    assert financial[0]["raw_fields"]["Amount_Original_Currency"] == 1000
    assert financial[0]["reference"]["Loan_Event_ID"] == 42
    assert financial[0]["raw_commodities"]["Transition_Minerals"] == "Nickel|Cobalt"
    loans = [row for row in rows if row["source_table"].endswith("!Loan_Event")]
    assert loans[0]["native_id"] != loans[1]["native_id"]
    assert loans[0]["raw_fields"]["Commitment_Date"] == datetime(2020, 1, 2)
    roles = [row for row in rows if row["source_table"].endswith("!Org_Role")]
    assert roles[0]["raw_fields"] == roles[1]["raw_fields"]
    assert roles[0]["native_id"].endswith("/occurrence-1")
    assert roles[1]["native_id"].endswith("/occurrence-2")
    assets = [row for row in rows if row["record_kind"] == "finance_asset"]
    assert {row["raw_fields"]["Equity_Percentage"] for row in assets} == {0.51, 0.49}
    assert all(row["reference"]["Loan_Event_IDs"] == "42|43" for row in assets)
    assert sum(p["rows"] for p in parsed["tables"] if p["role"] == "reference") == 4
    reconcile(parsed, rows)


def test_unknown_context_source_is_not_silently_empty(tmp_path):
    with pytest.raises(ValueError, match="No context parser"):
        parse(tmp_path, "unregistered_context")
