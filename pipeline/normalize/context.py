"""Source-native context records; no unit harmonization, filtering, or site inference."""

from __future__ import annotations

import csv
import hashlib
import io
import json
import xml.etree.ElementTree as ET
import zipfile
from collections import Counter
from datetime import date, datetime
from pathlib import Path
from urllib.parse import quote

from openpyxl import load_workbook

SOURCES = {"usgs_mcs_2026", "oecd_export", "aiddata_transition_v2"}
AIDDATA_MEMBER = (
    "AidData_CFTM_v2/AidData_Chinese_Financing_for_Transition_Minerals_Dataset_Version_2.0.xlsx"
)
AIDDATA_TABLES = {
    "Financial_Contribution": ("AidData_Record_ID", "Title", "Country_of_Activity", "finance"),
    "Loan_Event": ("Loan_Event_ID", "Title", "Country_of_Activity", "finance"),
    "Org_Role": ("Loan_Event_ID", "Organization_Name", "Host_Country", "finance"),
    "Mineral_Operation_Ownership": (
        "Investors_Ownership_ID",
        "Mining_Site",
        "Host_Country",
        "finance_asset",
    ),
}
OECD_DIMENSIONS = (
    "DATAFLOW",
    "REF_AREA",
    "COMMODITY",
    "POLICY_TYPE",
    "POLICY_VARIATION",
    "POLICY_DESCRIPTION",
    "TIME_PERIOD",
)
MCS_HEADERS = (
    "MCS chapter",
    "Section",
    "Commodity",
    "Country",
    "Statistics",
    "Statistics_detail",
    "Unit",
    "Year",
    "Value",
    "Notes",
    "Is critical mineral 2025",
    "Other notes",
)
AIDDATA_COMMODITIES = (
    "Transition_Minerals",
    "Primary_Minerals",
    "Subsidiary_Minerals",
    "Commodity",
)
AIDDATA_RELATIONS = (
    "AidData_Record_ID",
    "Parent_ID",
    "Loan_Event_ID",
    "Loan_Event_Tranche",
    "Associated_AidData_TUFF_Record_IDs",
    "Rescheduled_Loan_Record_ID",
    "Rescheduling_Event_ID",
    "Mining_Site_ID",
    "Investors_Ownership_ID",
    "Loan_Event_IDs",
    "All_Source_URLs",
    "All_Source_Titles",
    "All_Source_Publishers",
    "Original_Agreement_URL",
    "Original_Agreement_Title",
)


def _snapshot(root: Path, source: str) -> Path:
    paths = sorted((root / "data/raw" / source).glob("????-??-??/manifest.json"))
    if not paths:
        raise ValueError(f"No completed raw snapshot for {source}")
    return paths[-1].parent


def _one(directory: Path, pattern: str) -> Path:
    paths = sorted(directory.glob(pattern))
    if len(paths) != 1:
        raise ValueError(f"Expected one {pattern} in {directory}; found {len(paths)}")
    return paths[0]


def _text(value) -> str | None:
    return None if value is None or value == "" else str(value)


def _json_date(value):
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    raise TypeError(f"Unsupported source identity value: {type(value).__name__}")


def _digest(value) -> str:
    encoded = json.dumps(
        value,
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
        default=_json_date,
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _profile(result: dict, source: str, file: str, table: str, role: str) -> dict:
    profile = {"source_id": source, "file": file, "table": table, "rows": 0, "role": role}
    result["tables"].append(profile)
    return profile


def _validate_headers(headers: list[str], required, table: str) -> None:
    if len(headers) != len(set(headers)):
        raise ValueError(f"{table}: duplicate source column headings")
    if missing := set(required) - set(headers):
        raise ValueError(f"{table}: missing required columns {sorted(missing)}")


def _csv_rows(path: Path, encoding: str, required):
    with path.open(encoding=encoding, newline="") as stream:
        reader = csv.reader(stream)
        headers = next(reader, None)
        if headers is None:
            raise ValueError(f"{path.name}: missing CSV header")
        _validate_headers(headers, required, path.name)
        for row_number, values in enumerate(reader, 2):
            if not any(value != "" for value in values):
                continue
            if len(values) != len(headers):
                yield (
                    row_number,
                    {"__headers__": headers, "__cells__": values},
                    (f"CSV column count {len(values)} does not match header count {len(headers)}"),
                )
            else:
                yield row_number, dict(zip(headers, values, strict=True)), None


def _reject(result: dict, file: str, table: str, row_number: int, raw: dict, reason: str) -> None:
    result["rejects"].append(
        {
            "source_file": file,
            "source_table": table,
            "row_number": row_number,
            "reason": reason,
            "raw_fields": raw,
        }
    )


def _row(
    native_id: str,
    file: str,
    table: str,
    row_number: int,
    kind: str,
    raw: dict,
    name: str | None,
    country: str | None,
    commodities: dict,
    reference: dict,
    values: dict | None = None,
) -> dict:
    return {
        "native_id": native_id,
        "source_file": file,
        "source_table": table,
        "row_number": row_number,
        "record_kind": kind,
        "raw_name": name,
        "aliases": [],
        "lat": None,
        "lon": None,
        "location_precision": "none",
        "country": country,
        "raw_commodities": commodities,
        "raw_deposit_type": {},
        "raw_status": {},
        "raw_size_grade_text": values or {},
        "reference": reference,
        "raw_fields": raw,
    }


def _occurrence(base: str, counts: Counter) -> str:
    counts[base] += 1
    return f"{base}/occurrence-{counts[base]}"


def _mcs(root: Path, directory: Path, result: dict):
    path = _one(directory, "*.csv")
    file = path.relative_to(root / "data/raw").as_posix()
    profile = _profile(result, "usgs_mcs_2026", file, path.name, "primary")
    counts = Counter()
    for row_number, raw, error in _csv_rows(path, "cp1252", MCS_HEADERS):
        profile["rows"] += 1
        if error:
            _reject(result, file, path.name, row_number, raw, error)
            continue
        native = _occurrence(f"MCS2026/{_digest(raw)}", counts)
        yield _row(
            native,
            file,
            path.name,
            row_number,
            "country_commodity",
            raw,
            _text(raw["Commodity"]),
            _text(raw["Country"]),
            {"Commodity": raw["Commodity"]},
            {key: raw[key] for key in ("MCS chapter", "Notes", "Other notes")},
            {key: raw[key] for key in ("Statistics", "Statistics_detail", "Unit", "Year", "Value")},
        )
    result["notes"].append(
        "MCS IDs use the complete native-row SHA256 and occurrence ordinal: some statistical "
        "context keys repeat with different native content. All statistics, aggregates, years, "
        "units, symbols and comma-formatted values remain unchanged. CSV decoding is Windows-1252."
    )


def _oecd_structure(root: Path, path: Path, result: dict) -> None:
    file = path.relative_to(root / "data/raw").as_posix()
    tree = ET.parse(path)
    ns = {"s": "http://www.sdmx.org/resources/sdmxml/schemas/v2_1/structure"}
    codelists = tree.findall(".//s:Codelist", ns)
    if not codelists:
        raise ValueError("OECD structure XML has no codelists")
    for element in codelists:
        profile = _profile(result, "oecd_export", file, element.attrib["id"], "reference")
        profile["rows"] = len(element.findall("s:Code", ns))


def _oecd(root: Path, directory: Path, result: dict):
    files = sorted(directory.glob("*.csv"))
    years = [path.stem.rsplit("_", 1)[-1] for path in files]
    if sorted(years) != [str(year) for year in range(2009, 2025)]:
        raise ValueError("OECD requires exactly one annual CSV for every year 2009 through 2024")
    structure = _one(directory, "*.xml")
    _oecd_structure(root, structure, result)
    for path in files:
        file = path.relative_to(root / "data/raw").as_posix()
        profile = _profile(result, "oecd_export", file, path.name, "primary")
        year = path.stem.rsplit("_", 1)[-1]
        for row_number, raw, error in _csv_rows(
            path, "utf-8-sig", (*OECD_DIMENSIONS, "OBS_VALUE", "OBS_STATUS", "UNIT_MULT")
        ):
            profile["rows"] += 1
            if error:
                _reject(result, file, path.name, row_number, raw, error)
                continue
            if raw["TIME_PERIOD"] != year:
                _reject(
                    result,
                    file,
                    path.name,
                    row_number,
                    raw,
                    f"Observation TIME_PERIOD does not match annual partition {year}",
                )
                continue
            if any(raw[key] == "" for key in OECD_DIMENSIONS):
                _reject(
                    result, file, path.name, row_number, raw, "Missing OECD natural-key dimension"
                )
                continue
            native = f"observations/{_digest({key: raw[key] for key in OECD_DIMENSIONS})}"
            yield _row(
                native,
                file,
                path.name,
                row_number,
                "export_restriction",
                raw,
                _text(raw["COMMODITY"]),
                _text(raw["REF_AREA"]),
                {"COMMODITY": raw["COMMODITY"]},
                {
                    "DATAFLOW": raw["DATAFLOW"],
                    "structure_file": structure.relative_to(root / "data/raw").as_posix(),
                },
            )
    result["notes"].append(
        "Every OECD observation in all 16 annual partitions is retained, including no/blank "
        "observation values. IDs hash DATAFLOW and all six source dimensions; measurement values "
        "and observation-status attributes are not identity or mine status. CSVs stream one row "
        "at a time without retaining an observation list or identity set. SDMX codelists are "
        "reference tables, not additional export-restriction records."
    )


def _aiddata_id(sheet: str, raw: dict, id_field: str, counts: Counter) -> str:
    native_value = _text(raw[id_field])
    native = quote(native_value, safe="") if native_value is not None else "missing-native-id"
    if sheet == "Financial_Contribution" and native_value is not None:
        base = f"{sheet}/{native}"
    elif sheet == "Loan_Event" and native_value is not None:
        base = f"{sheet}/{native}/{_digest([raw[id_field], raw['Loan_Event_Tranche']])}"
    else:
        base = f"{sheet}/{native}/{_digest(raw)}"
    return _occurrence(base, counts)


def _aiddata(root: Path, directory: Path, result: dict):
    path = _one(directory, "*.zip")
    file = path.relative_to(root / "data/raw").as_posix()
    with zipfile.ZipFile(path) as archive:
        book = load_workbook(
            io.BytesIO(archive.read(AIDDATA_MEMBER)), read_only=True, data_only=True
        )
    try:
        if missing := set(AIDDATA_TABLES) - set(book.sheetnames):
            raise ValueError(f"AidData release missing data tables: {sorted(missing)}")
        if extra := set(book.sheetnames) - set(AIDDATA_TABLES) - {"Contents", "License"}:
            raise ValueError(f"AidData unclassified sheets require review: {sorted(extra)}")
        for sheet in book:
            table = f"{AIDDATA_MEMBER}!{sheet.title}"
            role = "primary" if sheet.title in AIDDATA_TABLES else "reference"
            profile = _profile(result, "aiddata_transition_v2", file, table, role)
            if role == "reference":
                profile["rows"] = sum(
                    any(value is not None and value != "" for value in row)
                    for row in sheet.iter_rows(values_only=True)
                )
                continue
            id_field, name_field, country_field, kind = AIDDATA_TABLES[sheet.title]
            rows = sheet.iter_rows(values_only=True)
            headers = list(next(rows))
            required = [id_field, name_field, country_field]
            if sheet.title == "Loan_Event":
                required.extend(("Loan_Event_Tranche", "Primary_Minerals", "Subsidiary_Minerals"))
            if sheet.title in {"Financial_Contribution", "Loan_Event", "Org_Role"}:
                required.append("Transition_Minerals")
            _validate_headers(headers, required, sheet.title)
            counts = Counter()
            for row_number, values in enumerate(rows, 2):
                if not any(value is not None and value != "" for value in values):
                    continue
                profile["rows"] += 1
                if len(values) != len(headers):
                    _reject(
                        result,
                        file,
                        table,
                        row_number,
                        {"__headers__": headers, "__cells__": list(values)},
                        "Workbook row width differs from header width",
                    )
                    continue
                raw = dict(zip(headers, values, strict=True))
                native = _aiddata_id(sheet.title, raw, id_field, counts)
                yield _row(
                    native,
                    file,
                    table,
                    row_number,
                    kind,
                    raw,
                    _text(raw[name_field]),
                    _text(raw[country_field]),
                    {key: raw[key] for key in AIDDATA_COMMODITIES if key in raw},
                    {key: raw[key] for key in AIDDATA_RELATIONS if key in raw},
                )
    finally:
        book.close()
    result["notes"].append(
        "AidData's four data sheets all emit primary staging records: Financial_Contribution, "
        "Loan_Event and Org_Role remain finance records; Mineral_Operation_Ownership remains "
        "finance_asset ownership evidence, not a deduplicated site. No sheet contains coordinate "
        "columns, so all records are unlocated. Original relationship IDs, tranche values, "
        "ownership rows, finance statuses, amounts and currencies remain in raw_fields. Status "
        "never becomes mine raw_status. Exact duplicate relationship rows are preserved with "
        "content-hash/occurrence IDs; unique financial IDs and loan ID/tranche keys retain their "
        "native identity. Missing native IDs fall back to complete-row hashes. Contents and "
        "License are counted as reference sheets; the methodology PDF and archive resource "
        "forks do not contain input data tables."
    )


def parse(root: Path, source_id: str) -> dict:
    """Return a one-pass iterator; table counts and rejects finalize on exhaustion."""
    if source_id not in SOURCES:
        raise ValueError(f"No context parser for {source_id}")
    directory = _snapshot(root, source_id)
    result = {"rows": (), "tables": [], "notes": [], "rejects": []}
    result["notes"].append(
        "Rows retain source-native values and are not harmonized, pilot-filtered or mapped. "
        "CSV row numbers count logical records including the header; workbook row numbers "
        "are Excel row positions. Official critical-list HTML was consumed by the vocabulary "
        "step and is not an additional context staging table."
    )
    handler = {"usgs_mcs_2026": _mcs, "oecd_export": _oecd, "aiddata_transition_v2": _aiddata}[
        source_id
    ]
    result["rows"] = handler(root, directory, result)
    return result
