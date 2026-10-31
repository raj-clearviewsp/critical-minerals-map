"""Read-only, exhaustive field inventories for the Atlas tabular snapshots.

This module preserves raw strings and counts cells, not canonical commodities or
sites. It deliberately leaves compound-value tokenization to the vocabulary
resolver. Repeated rows and values in distinct source tables remain separate.
"""

from __future__ import annotations

import csv
import io
import json
import xml.etree.ElementTree as ET
import zipfile
from collections import Counter
from pathlib import Path
from typing import Any

from openpyxl import load_workbook
from openpyxl.utils import get_column_letter

SOURCE_IDS = (
    "gseu_crm",
    "ga_global_ree",
    "ga_critical_2025",
    "ga_critical_2025_arcgis",
    "icmm",
    "wapha",
    "jasansky",
    "usgs_mcs_2026",
    "oecd_export",
    "aiddata_transition_v2",
)


class Inventory:
    def __init__(self, root: Path):
        self.raw = root / "data" / "raw"
        self.counts: Counter = Counter()
        self.tables: list[dict] = []
        self.notes: list[str] = []
        self.code_labels: list[dict] = []

    def filename(self, path: Path) -> str:
        return path.relative_to(self.raw).as_posix()

    def table(
        self,
        source: str,
        path: Path,
        name: str,
        headers: list,
        rows: Any,
        fields: dict[str, str],
        **metadata: Any,
    ) -> None:
        """Read every row once and count every nonempty selected cell verbatim."""
        headers = [str(v) if v is not None else "" for v in headers]
        selected = [(i, h, fields[h]) for i, h in enumerate(headers) if h in fields]
        missing = set(fields) - set(headers)
        if missing:
            raise ValueError(f"{source}/{name}: required fields absent: {sorted(missing)}")
        filename = self.filename(path)
        row_count = 0
        nonempty = Counter()
        for row in rows:
            if not any(value is not None and value != "" for value in row):
                continue
            row_count += 1
            for index, header, kind in selected:
                value = row[index] if index < len(row) else None
                if value is None or value == "":
                    continue
                self.counts[(source, filename, name, header, kind, str(value))] += 1
                nonempty[index] += 1
        self.tables.append(
            {
                "source_id": source,
                "file": self.filename(path),
                "table": name,
                "rows": row_count,
                "fields": headers,
                "selected_fields": [
                    {
                        "field": h,
                        "column": get_column_letter(i + 1),
                        "kind": kind,
                        "nonempty_cells": nonempty[i],
                        "empty_cells": row_count - nonempty[i],
                    }
                    for i, h, kind in selected
                ],
                **metadata,
            }
        )

    def workbook(
        self, source: str, path: Path, spec: dict, content: Any = None, member: str | None = None
    ) -> None:
        book = load_workbook(
            content if content is not None else path, read_only=True, data_only=True
        )
        try:
            for name, config in spec.items():
                sheet = book[name]
                header_row, fields = config[:2]
                headers = list(
                    next(sheet.iter_rows(min_row=header_row, max_row=header_row, values_only=True))
                )
                if len(config) == 3:
                    for index, value in config[2].items():
                        headers[index] = value
                self.table(
                    source,
                    path,
                    f"{member}!{name}" if member else name,
                    headers,
                    sheet.iter_rows(min_row=header_row + 1, values_only=True),
                    fields,
                    header_row=header_row,
                    archive_member=member,
                )
            omitted = sorted(set(book.sheetnames) - set(spec))
            if omitted:
                self.notes.append(
                    f"{source}/{self.filename(path)}: non-record sheets omitted: {omitted}."
                )
        finally:
            book.close()

    def csv_table(
        self,
        source: str,
        path: Path,
        stream: Any,
        fields: dict[str, str],
        name: str | None = None,
        encoding: str = "utf-8-sig",
    ) -> None:
        reader = csv.reader(stream)
        self.table(source, path, name or path.name, next(reader), reader, fields, encoding=encoding)

    def result(self) -> dict:
        keys = ("source_id", "file", "table", "field", "kind", "raw_value")
        return {
            "observations": [
                dict(zip(keys, key, strict=True), count=count)
                for key, count in sorted(self.counts.items())
            ],
            "tables": sorted(self.tables, key=lambda x: (x["source_id"], x["file"], x["table"])),
            "notes": sorted(self.notes),
            "code_labels": sorted(
                self.code_labels, key=lambda x: (x["source_id"], x["table"], x["code"])
            ),
        }


def _one(directory: Path, pattern: str) -> Path:
    paths = sorted(directory.glob(pattern))
    if len(paths) != 1:
        raise ValueError(f"Expected exactly one {pattern} in {directory}; found {len(paths)}")
    return paths[0]


def _snapshot(raw: Path, source: str) -> Path:
    """Select newest committed raw snapshot, never a fetcher's staging directory."""
    dirs = sorted(p.parent for p in (raw / source).glob("????-??-??/manifest.json"))
    if not dirs:
        raise ValueError(f"No raw manifest found for {source}")
    return dirs[-1]


def _ga_arcgis(audit: Inventory, directory: Path) -> None:
    for path in sorted(directory.glob("layer_*_page_*.json")):
        payload = json.loads(path.read_text())
        features = payload["features"]
        headers = (
            list(features[0]["attributes"])
            if features
            else [item["name"] for item in payload.get("fields", [])]
        )
        rows = ([feature["attributes"].get(h) for h in headers] for feature in features)
        audit.table(
            "ga_critical_2025_arcgis",
            path,
            path.stem,
            headers,
            rows,
            {"commodities": "commodity", "status": "status"},
        )
    audit.notes.append(
        "ga_critical_2025_arcgis: Australia is the declared source scope; "
        "no country-value field exists. State codes are admin1, not countries."
    )


def _jasansky(audit: Inventory, path: Path) -> None:
    with zipfile.ZipFile(path) as archive:
        for member in sorted(n for n in archive.namelist() if n.endswith(".csv")):
            with io.TextIOWrapper(archive.open(member), encoding="utf-8-sig") as stream:
                reader = csv.reader(stream)
                headers = next(reader)
                commodity_fields = {
                    "material",
                    "commodity",
                    "input",
                    "output",
                    "product",
                    "material_id",
                    "material_name",
                    "material_id_agg",
                    "material_name_agg",
                    "materials_contained",
                }
                fields = {h: "commodity" for h in headers if h in commodity_fields}
                if "export_to" in headers:
                    fields["export_to"] = "country"
                audit.table(
                    "jasansky",
                    path,
                    member,
                    headers,
                    reader,
                    fields,
                    archive_member=member,
                    encoding="utf-8-sig",
                )
            if member == "data/material_ids.csv":
                with io.TextIOWrapper(archive.open(member), encoding="utf-8-sig") as stream:
                    for row in csv.DictReader(stream):
                        audit.code_labels.append(
                            {
                                "source_id": "jasansky",
                                "file": audit.filename(path),
                                "table": member,
                                "code": row["material_id"],
                                "label": row["material_name"],
                                "kind": "commodity",
                                "code_field": "material_id",
                                "label_field": "material_name",
                            }
                        )
        member = "materials_covered.xlsx"
        audit.workbook(
            "jasansky",
            path,
            {
                "Sheet1": (1, {"material_id": "commodity", "material_name": "commodity"}),
            },
            io.BytesIO(archive.read(member)),
            member,
        )
    audit.notes.append(
        "jasansky: all CSV tables scanned; material_ids.csv is the full source "
        "codebook, materials_covered.xlsx is its coverage subset. facilities.gpkg "
        "is inventoried by the geospatial extractor. variables_descriptions.xlsx "
        "describes fields rather than records and is not counted as observations."
    )


def _oecd(audit: Inventory, directory: Path) -> None:
    paths = sorted(directory.glob("*.csv"))
    if {p.stem.rsplit("_", 1)[-1] for p in paths} != {str(y) for y in range(2009, 2025)}:
        raise ValueError("OECD full inventory requires every annual CSV from 2009 through 2024")
    for path in paths:
        with path.open(encoding="utf-8-sig", newline="") as stream:
            audit.csv_table(
                "oecd_export", path, stream, {"COMMODITY": "commodity", "REF_AREA": "country"}
            )
    observed = {
        kind: {key[-1] for key in audit.counts if key[0] == "oecd_export" and key[-2] == kind}
        for kind in ("commodity", "country")
    }
    path = _one(directory, "*.xml")
    tree = ET.parse(path)
    ns = {
        "s": "http://www.sdmx.org/resources/sdmxml/schemas/v2_1/structure",
        "c": "http://www.sdmx.org/resources/sdmxml/schemas/v2_1/common",
    }
    for codelist, kind in (("CL_PRODUCT_HS2007", "commodity"), ("CL_AREA", "country")):
        element = tree.find(f".//s:Codelist[@id='{codelist}']", ns)
        if element is None:
            raise ValueError(f"OECD structure lacks {codelist}")
        labels = {}
        for code in element.findall("s:Code", ns):
            names = code.findall("c:Name", ns)
            english = next(
                (n for n in names if n.get("{http://www.w3.org/XML/1998/namespace}lang") == "en"),
                None,
            )
            if english is not None:
                labels[code.attrib["id"]] = english.text or ""
        missing = observed[kind] - set(labels)
        if missing:
            raise ValueError(f"OECD {codelist} missing observed codes: {sorted(missing)}")
        for code in sorted(observed[kind]):
            audit.code_labels.append(
                {
                    "source_id": "oecd_export",
                    "file": audit.filename(path),
                    "table": codelist,
                    "code": code,
                    "label": labels[code],
                    "kind": kind,
                    "code_field": "Code/@id",
                    "label_field": "Name[@xml:lang='en']",
                }
            )
        audit.tables.append(
            {
                "source_id": "oecd_export",
                "file": audit.filename(path),
                "table": codelist,
                "rows": len(labels),
                "fields": ["Code/@id", "Name[@xml:lang='en']"],
                "observed_codes": len(observed[kind]),
                "selected_fields": [],
            }
        )
    audit.notes.append(
        "oecd_export: COMMODITY uses CL_PRODUCT_HS2007; REF_AREA uses CL_AREA. "
        "Every observed code is joined to its exact English structural label. "
        "OBS_STATUS describes observations, not mine operating status, so is excluded."
    )


def _aiddata(audit: Inventory, path: Path) -> None:
    member = (
        "AidData_CFTM_v2/AidData_Chinese_Financing_for_Transition_Minerals_Dataset_Version_2.0.xlsx"
    )
    countries = {
        name: "country"
        for name in (
            "Financier_Country",
            "Country_of_Activity",
            "Country_of_Activity_ISO3",
            "DRA_Country_of_Inc",
            "DRA_Country_of_Inc_ISO3",
            "Overseas_Jurisdiction_ISO3",
        )
    }
    common = {
        "Transition_Minerals": "commodity",
        "Commodity": "commodity",
        "Status": "status",
        **countries,
    }
    specs = {
        "Financial_Contribution": (1, common),
        "Loan_Event": (
            1,
            {**common, "Primary_Minerals": "commodity", "Subsidiary_Minerals": "commodity"},
        ),
        "Org_Role": (1, {"Transition_Minerals": "commodity", "Host_Country": "country"}),
        "Mineral_Operation_Ownership": (1, {"Host_Country": "country"}),
    }
    with zipfile.ZipFile(path) as archive:
        audit.workbook(
            "aiddata_transition_v2", path, specs, io.BytesIO(archive.read(member)), member
        )
    # Preserve exhaustive finance states as context, never as mine statuses.
    finance_states = []
    for identity in list(audit.counts):
        if identity[0] == "aiddata_transition_v2" and identity[4] == "status":
            finance_states.append(
                {
                    "table": identity[2],
                    "field": identity[3],
                    "raw_value": identity[5],
                    "count": audit.counts.pop(identity),
                }
            )
    for table in audit.tables:
        if table["source_id"] == "aiddata_transition_v2":
            table["context_only_fields"] = [
                {**field, "kind": "finance_status"}
                for field in table["selected_fields"]
                if field["field"] == "Status"
            ]
            table["selected_fields"] = [
                field for field in table["selected_fields"] if field["field"] != "Status"
            ]
    audit.notes.append(
        "aiddata_transition_v2: finance-only state inventory (not mine status): "
        + json.dumps(sorted(finance_states, key=lambda x: (x["table"], x["raw_value"])))
    )
    audit.notes.append(
        "aiddata_transition_v2: Status concerns financed project/activity progress, "
        "not mine operating status. Commodity identifies collateral/repayment "
        "commodities and remains separate from Transition_Minerals, Primary_Minerals "
        "and Subsidiary_Minerals. Country roles are preserved by source field. "
        "Mineral_Operation_Ownership has no commodity field; project/company names "
        "and descriptive prose are not mined for commodity terms."
    )


def extract(root: Path) -> dict:
    """Return raw-value counters, scan evidence, and source code-label metadata."""
    audit = Inventory(root)
    dirs = {sid: _snapshot(audit.raw, sid) for sid in SOURCE_IDS}
    audit.workbook(
        "gseu_crm",
        _one(dirs["gseu_crm"], "*.xlsx"),
        {
            "Data": (
                2,
                {
                    "CRM": "commodity",
                    "Type of commodity reported": "commodity",
                    "Resources - type of commodity reported": "commodity",
                    "Reserves - type of commodity reported": "commodity",
                    "Production - type of commodity reported": "commodity",
                    "Deposit type": "deposit_type",
                    "Deposit mining activity status": "status",
                    "Country": "country",
                    "ISO3": "country",
                },
                {12: "Deposit mining activity status"},
            ),
            "Deposit sizes": (5, {"Commodity (*)": "commodity", "Abbreviation": "commodity"}),
        },
    )
    audit.notes.append(
        "gseu_crm: Data column M takes its exact status label from row1; other "
        "headers are row2. Deposit sizes is a source code/threshold table, distinct "
        "from deposit records; chemical reporting forms are inventoried separately."
    )
    audit.workbook(
        "ga_global_ree",
        _one(dirs["ga_global_ree"], "*.xlsx"),
        {
            "Deposits": (1, {"Country": "country", "Deposit type": "deposit_type"}),
        },
    )
    audit.notes.append(
        "ga_global_ree: the global REE compilation has no commodity-code column. "
        "All three repeated Deposit type columns are scanned. Element is a tectonic "
        "geography field, not a chemical symbol. REE and individual oxide assay "
        "column headings remain in table.fields; no commodity row values are "
        "fabricated from the declared REE scope. CRIRSCO compliance fields are "
        "not operating status."
    )
    audit.workbook(
        "ga_critical_2025",
        _one(dirs["ga_critical_2025"], "*.xlsx"),
        {
            "CM Mines and Deposits 2025": (
                13,
                {"Commodity Group": "commodity", "Status": "status"},
            ),
        },
    )
    audit.notes.append(
        "ga_critical_2025: Australia is the source scope, not a country-value "
        "column. Compound Commodity Group values and +/- qualifiers are retained "
        "verbatim. The About sheet holds summary counts rather than deposit records."
    )
    _ga_arcgis(audit, dirs["ga_critical_2025_arcgis"])
    audit.workbook(
        "icmm",
        _one(dirs["icmm"], "*.xlsx"),
        {
            "Global Mining Dataset v1.5": (
                1,
                {
                    "Primary Commodity": "commodity",
                    "Secondary Commodity ": "commodity",
                    "Other Commodities": "commodity",
                    "Country": "country",
                },
            ),
        },
    )
    audit.notes.append(
        "icmm: Asset Type is a facility category, not mine operating status. "
        "Names and source-provider codes are excluded from commodity extraction."
    )
    for path in sorted(dirs["wapha"].glob("*.xlsx")):
        audit.workbook(
            "wapha", path, {"Sheet1": (1, {"Commodity": "commodity", "Status": "status"})}
        )
    audit.notes.append(
        "wapha: both active and inactive workbooks scanned exhaustively; "
        "neither includes a country-value column."
    )
    _jasansky(audit, _one(dirs["jasansky"], "*.zip"))
    path = _one(dirs["usgs_mcs_2026"], "*.csv")
    with path.open(encoding="cp1252", newline="") as stream:
        audit.csv_table(
            "usgs_mcs_2026",
            path,
            stream,
            {"Commodity": "commodity", "Country": "country"},
            encoding="cp1252",
        )
    audit.notes.append(
        "usgs_mcs_2026: CSV is Windows-1252 (e.g. byte0x97 em dash); decoded "
        "without replacement. Commodity/Country are scanned, not commodity mentions "
        "in Notes or Statistics_detail. No deposit or operating-status field exists."
    )
    _oecd(audit, dirs["oecd_export"])
    _aiddata(audit, _one(dirs["aiddata_transition_v2"], "*.zip"))
    return audit.result()
