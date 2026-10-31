"""Lossless source records for deposit workbooks and facility inventories.

These adapters read only verified local snapshots. They do not resolve controlled
vocabularies, filter commodities, assign maturity, or create canonical sites.
Table counts are finalized when the returned row iterator is exhausted.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import math
import re
import struct
import tempfile
import zipfile
from collections import Counter, defaultdict
from datetime import date, datetime
from pathlib import Path

import pyogrio
from openpyxl import load_workbook
from openpyxl.utils import get_column_letter

from pipeline.vocab.tabular import _one, _snapshot

SOURCE_IDS = (
    "gseu_crm",
    "ga_global_ree",
    "ga_critical_2025",
    "ga_critical_2025_arcgis",
    "icmm",
    "wapha",
    "jasansky",
)


def _value(value):
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, bytes):
        return value.hex()
    if isinstance(value, float) and not math.isfinite(value):
        return str(value)
    return value


def _headers(values):
    """Keep duplicate, whitespace-bearing and unnamed columns independently."""
    labels = [str(v) if v is not None else "" for v in values]
    counts = Counter(labels)
    return [
        f"{label} [column {get_column_letter(i + 1)}]" if not label or counts[label] > 1 else label
        for i, label in enumerate(labels)
    ]


def _select(row, names):
    return {name: row[name] for name in names if name in row}


def _coordinates(lon, lat):
    try:
        x, y = float(lon), float(lat)
    except (ValueError, TypeError):
        return None, None
    if not (math.isfinite(x) and math.isfinite(y) and -180 <= x <= 180 and -90 <= y <= 90):
        return None, None
    return x, y


def _point_wkb(geometry):
    """Use only a supplied point or singleton multipoint; never a centroid."""
    if not geometry or len(geometry) < 5:
        return None, None
    order = "<" if geometry[0] == 1 else ">"
    kind = struct.unpack_from(order + "I", geometry, 1)[0]
    if kind == 1 and len(geometry) >= 21:
        return _coordinates(*struct.unpack_from(order + "dd", geometry, 5))
    if kind == 4 and len(geometry) >= 9:
        count = struct.unpack_from(order + "I", geometry, 5)[0]
        if count == 1:
            return _point_wkb(geometry[9:])
    return None, None


class _Parse:
    def __init__(self, root, source):
        self.raw = root / "data/raw"
        self.source = source
        self.tables = []
        self.notes = []
        self.rejects = []
        self._file_hashes = {}

    def file(self, path):
        return path.relative_to(self.raw).as_posix()

    def row_id(self, path, table, number, scope=None):
        """Bind positional identity to the entire immutable workbook, once per file."""
        if path not in self._file_hashes:
            digest = hashlib.sha256()
            with path.open("rb") as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(chunk)
            self._file_hashes[path] = digest.hexdigest()
        prefix = f"{scope}:" if scope else ""
        return f"{prefix}{table}:sha256:{self._file_hashes[path]}:row:{number}"

    def table(self, path, table, role, **extra):
        profile = {
            "source_id": self.source,
            "file": self.file(path),
            "table": table,
            "rows": 0,
            "role": role,
            **extra,
        }
        self.tables.append(profile)
        return profile

    def reject(self, path, table, number, row, reason):
        self.rejects.append(
            {
                "source_file": self.file(path),
                "source_table": table,
                "row_number": number,
                "reason": reason,
                "raw_fields": row,
            }
        )

    def record(
        self,
        path,
        table,
        number,
        row,
        *,
        native_id,
        name,
        kind="deposit",
        lon=None,
        lat=None,
        country=None,
        commodities=(),
        deposits=(),
        statuses=(),
        sizes=(),
        aliases=(),
        reference=None,
        geometry=None,
    ):
        # The common writer validates original coordinate cells and records issues.
        # Do not turn a malformed or incomplete pair into an unexplained null here.
        x, y = lon, lat
        result = {
            "native_id": str(native_id),
            "source_file": self.file(path),
            "source_table": table,
            "row_number": number,
            "record_kind": kind,
            "raw_name": name,
            "aliases": [v for v in aliases if v not in (None, "")],
            "lat": y,
            "lon": x,
            "location_precision": "none" if x is None and y is None else None,
            "country": country,
            "raw_commodities": _select(row, commodities),
            "raw_deposit_type": _select(row, deposits),
            "raw_status": _select(row, statuses),
            "raw_size_grade_text": _select(row, sizes),
            "reference": reference or {},
            "raw_fields": row,
        }
        if geometry is not None:
            result["raw_geometry"] = geometry.hex() if isinstance(geometry, bytes) else geometry
        return result


def _reference_sheets(parser, path, book, primary):
    for name in book.sheetnames:
        if name in primary:
            continue
        profile = parser.table(path, name, "reference", header_row=None)
        profile["rows"] = sum(
            any(v not in (None, "") for v in row) for row in book[name].iter_rows(values_only=True)
        )


def _workbook_rows(parser, path, sheet, header_row, required, overrides=None):
    """A read-only stream, closed even if its consumer stops early."""
    book = load_workbook(path, read_only=True, data_only=True)
    try:
        ws = book[sheet]
        original = list(
            next(ws.iter_rows(min_row=header_row, max_row=header_row, values_only=True))
        )
        headers = list(original)
        for index, value in (overrides or {}).items():
            headers[index] = value
        if not set(required) <= set(headers):
            raise ValueError(f"{parser.source}/{sheet}: required columns missing")
        names = _headers(headers)
        profile = parser.table(
            path, sheet, "primary", header_row=header_row, fields=names, original_headers=original
        )
        _reference_sheets(parser, path, book, {sheet})
        for number, values in enumerate(
            ws.iter_rows(min_row=header_row + 1, values_only=True), header_row + 1
        ):
            if not any(v not in (None, "") for v in values):
                continue
            profile["rows"] += 1
            yield number, dict(zip(names, map(_value, values), strict=True))
    finally:
        book.close()


def _gseu(parser, directory):
    path = _one(directory, "*.xlsx")
    fields = ("Deposit name", "Country", "ISO3", "CRM", "Deposit size", "longitude", "latitude")
    parser.notes.append(
        "GSEU fallback IDs combine full source-file SHA-256, sheet and row position; "
        "they are not publisher deposit IDs and change when workbook bytes change. "
        "Commodity-specific repeated deposits remain separate. Size classes and "
        "minimum-estimate thresholds are raw evidence, not computed resources."
    )
    for number, row in _workbook_rows(
        parser, path, "Data", 2, fields, {12: "Deposit mining activity status"}
    ):
        if row["Deposit name"] in (None, ""):
            parser.reject(path, "Data", number, row, "Non-record explanatory row: no deposit name")
            continue
        yield parser.record(
            path,
            "Data",
            number,
            row,
            native_id=parser.row_id(path, "Data", number),
            name=row["Deposit name"],
            lon=row["longitude"],
            lat=row["latitude"],
            country=row["Country"],
            commodities=(
                "CRM",
                "Type of commodity reported",
                "Resources - type of commodity reported",
                "Reserves - type of commodity reported",
                "Production - type of commodity reported",
            ),
            deposits=("Deposit type",),
            statuses=("Deposit mining activity status",),
            sizes=[
                k
                for k in row
                if k
                not in {
                    "Deposit name",
                    "Country",
                    "ISO3",
                    "Deposit type",
                    "CRM",
                    "longitude",
                    "latitude",
                    "Deposit mining activity status",
                }
            ],
            reference={
                "size_classes": {"source_file": parser.file(path), "source_table": "Deposit sizes"}
            },
        )


def _ga_global(parser, directory):
    path = _one(directory, "*.xlsx")
    parser.notes.extend(
        [
            "GA global REE's first column is literally Coastal and contains deposit names. "
            "Duplicate headings retain worksheet column suffixes; Element is tectonic geography.",
            "GA global REE fallback IDs combine full source-file SHA-256, sheet and row position; "
            "they are not publisher deposit IDs and change when workbook bytes change.",
            "raw_commodities.source_scope=REE is the documented compilation scope, not an observed "
            "cell or proof of production: Global rare earth element deposits - a compilation, "
            "Geoscience Australia Record 2024/03, https://pid.geoscience.gov.au/dataset/ga/148953.",
        ]
    )
    for number, row in _workbook_rows(
        parser, path, "Deposits", 1, ("Coastal", "Country", "Longitude", "Latitude", "Deposit type")
    ):
        if row["Coastal"] in (None, ""):
            parser.reject(path, "Deposits", number, row, "Missing source deposit name")
            continue
        record = parser.record(
            path,
            "Deposits",
            number,
            row,
            native_id=parser.row_id(path, "Deposits", number),
            name=row["Coastal"],
            lon=row["Longitude"],
            lat=row["Latitude"],
            country=row["Country"],
            deposits=[k for k in row if k == "Deposit type" or k.startswith("Deposit type [")],
            sizes=[
                k for k in row if any(t in k for t in ("(Mt)", "(kt)", "(%)", "(ppm)", "CRIRSCO"))
            ],
            reference={
                "fields": {k: v for k, v in row.items() if k.startswith("Source(s) of data")},
                "bibliography": {"source_file": parser.file(path), "source_table": "References"},
                "source_scope": "https://pid.geoscience.gov.au/dataset/ga/148953",
            },
        )
        record["raw_commodities"] = {"source_scope": "REE"}
        yield record


def _ga_workbook(parser, directory):
    path = _one(directory, "*.xlsx")
    table = "CM Mines and Deposits 2025"
    parser.notes.append(
        "GA workbook rows have no publisher ID or country column; fallback IDs combine "
        "full source-file SHA-256, sheet and row position, changing when workbook bytes "
        "change. Country stays null. Australia is the declared source "
        "scope; State is preserved as a source field. Status does not establish "
        "production of every listed critical mineral."
    )
    for number, row in _workbook_rows(
        parser, path, table, 13, ("Name", "Latitude", "Longitude", "Status", "Commodity Group")
    ):
        if row["Name"] in (None, ""):
            parser.reject(path, table, number, row, "Missing source project name")
            continue
        yield parser.record(
            path,
            table,
            number,
            row,
            native_id=parser.row_id(path, table, number),
            name=row["Name"],
            kind="deposit" if row["Status"] == "Mineral deposit" else "facility",
            lon=row["Longitude"],
            lat=row["Latitude"],
            commodities=("Commodity Group",),
            statuses=("Status",),
        )


def _arcgis(parser, directory):
    paths = sorted(directory.glob("layer_*_page_*.json"))
    if not paths:
        raise ValueError("GA ArcGIS snapshot has no retained pages")
    for path in paths:
        layer = re.fullmatch(r"layer_(\d+)_page_\d+\.json", path.name).group(1)
        payload = json.loads(path.read_text())
        profile = parser.table(path, path.stem, "primary", layer_id=int(layer))
        for number, feature in enumerate(payload["features"], 1):
            profile["rows"] += 1
            row = dict(feature["attributes"])
            if any(k not in row for k in ("objectid", "projectname", "status", "commodities")):
                raise ValueError("GA ArcGIS required attributes missing")
            if row["objectid"] is None:
                parser.reject(path, path.stem, number, row, "Missing ArcGIS objectid")
                continue
            geometry = feature.get("geometry")
            spatial_reference = payload.get("spatialReference", {})
            lon, lat = row.get("longitude"), row.get("latitude")
            if (
                geometry
                and spatial_reference.get("latestWkid", spatial_reference.get("wkid")) == 4326
            ):
                lon, lat = geometry.get("x"), geometry.get("y")
            yield parser.record(
                path,
                path.stem,
                number,
                row,
                native_id=f"layer:{layer}:objectid:{row['objectid']}",
                name=row["projectname"],
                kind="deposit" if layer == "7" else "facility",
                lon=lon,
                lat=lat,
                commodities=("commodities",),
                statuses=("status",),
                reference={
                    "source": row.get("source"),
                    "layer_id": int(layer),
                    "spatial_reference": payload.get("spatialReference"),
                },
                geometry=geometry,
            )
    for path in sorted(directory.glob("*.json")):
        if path in paths or path.name == "manifest.json":
            continue
        profile = parser.table(path, path.stem, "reference")
        profile["rows"] = 1
    parser.notes.append(
        "GA ArcGIS IDs include layer ID. Coordinates use retained WGS84 response points "
        "where explicitly declared, otherwise original geographic attributes; response "
        "geometry, original coordinate fields and spatial reference remain evidence. "
        "Country is absent and remains null."
    )


def _icmm(parser, directory):
    path = _one(directory, "*.xlsx")
    table = "Global Mining Dataset v1.5"
    parser.notes.append(
        "ICMM Asset Type, Confidence Factor and Contains are retained verbatim. "
        "Asset type is not mine status or location precision. Group Names are kept "
        "as an unsplit source alias field; proprietary comparison tables are reference only."
    )
    for number, row in _workbook_rows(
        parser,
        path,
        table,
        1,
        ("ICMMID", "Mine Name", "Asset Type", "Country", "Latitude", "Longitude"),
    ):
        if row["ICMMID"] in (None, ""):
            parser.reject(path, table, number, row, "Missing ICMMID")
            continue
        yield parser.record(
            path,
            table,
            number,
            row,
            native_id=row["ICMMID"],
            name=row["Mine Name"],
            kind="facility",
            lon=row["Longitude"],
            lat=row["Latitude"],
            country=row["Country"],
            commodities=("Primary Commodity", "Secondary Commodity ", "Other Commodities"),
            aliases=(row.get("Group Names"),),
            reference={"Contains": row.get("Contains")},
        )


def _wapha(parser, directory):
    parser.notes.append(
        "WAPHA source files have no native ID or country. Fallback IDs combine active/inactive "
        "scope, full source-file SHA-256, sheet and row position; IDs change when workbook "
        "bytes change. Repeated names and "
        "coordinates remain separate. Status is historical source wording."
    )
    for scope in ("active", "inactive"):
        path = _one(directory, f"world_{scope}_mines_*.xlsx")
        name = "Mine Name" if scope == "active" else "Mine name"
        for number, row in _workbook_rows(
            parser, path, "Sheet1", 1, (name, "Status", "Commodity", "Longitude", "Latitude")
        ):
            yield parser.record(
                path,
                "Sheet1",
                number,
                row,
                native_id=parser.row_id(path, "Sheet1", number, scope),
                name=row[name],
                kind="facility",
                lon=row["Longitude"],
                lat=row["Latitude"],
                commodities=("Commodity",),
                statuses=("Status",),
            )


def _jasansky(parser, directory):
    path = _one(directory, "*.zip")
    linked = defaultdict(lambda: defaultdict(list))
    references = {}
    parser.notes.append(
        "Jasansky facility_id and sub_site fields remain intact. Region/Company "
        "rows are source_group records. Child production, ownership and capacity "
        "tables are linked source evidence, not new facilities or current facts. "
        "Only point/singleton multipoint geometries provide coordinates."
    )
    with (
        zipfile.ZipFile(path) as archive,
        tempfile.TemporaryDirectory(prefix="atlas-normalize-") as temp,
    ):
        packages = [m for m in archive.namelist() if m.endswith(".gpkg")]
        if len(packages) != 1:
            raise ValueError("Jasansky archive must contain exactly one GeoPackage")
        member = packages[0]
        local = Path(temp) / "facilities.gpkg"
        local.write_bytes(archive.read(member))
        metadata, data = pyogrio.read_arrow(local, layer="facilities", return_fids=True)
        if metadata["crs"] != "EPSG:4326":
            raise ValueError("Unexpected Jasansky CRS; no implicit coordinate conversion")
        geometry_name = metadata["geometry_name"]
        ids = set(data["facility_id"].to_pylist())
        for name in sorted(m for m in archive.namelist() if m.endswith(".csv")):
            with io.TextIOWrapper(archive.open(name), encoding="utf-8-sig", newline="") as stream:
                reader = csv.DictReader(stream)
                role = "support" if "facility_id" in reader.fieldnames else "reference"
                profile = parser.table(
                    path, name, role, fields=reader.fieldnames, archive_member=name
                )
                for number, row in enumerate(reader, 2):
                    profile["rows"] += 1
                    if role == "support":
                        if row["facility_id"] not in ids:
                            raise ValueError(
                                f"Orphan Jasansky facility_id in {name}: {row['facility_id']}"
                            )
                        linked[row["facility_id"]][name].append(
                            {
                                "source_file": parser.file(path),
                                "source_table": name,
                                "row_number": number,
                                "raw_fields": row,
                            }
                        )
                    elif name == "data/source_ids.csv":
                        references[row["source_id"]] = row
        for name in sorted(m for m in archive.namelist() if m.endswith(".xlsx")):
            book = load_workbook(io.BytesIO(archive.read(name)), read_only=True, data_only=True)
            try:
                for sheet in book.sheetnames:
                    profile = parser.table(
                        path, f"{name}!{sheet}", "reference", archive_member=name
                    )
                    profile["rows"] = sum(
                        any(v not in (None, "") for v in r)
                        for r in book[sheet].iter_rows(values_only=True)
                    )
            finally:
                book.close()
        table = member + ":facilities"
        profile = parser.table(
            path, table, "primary", fields=data.column_names, archive_member=member
        )
        for number, original in enumerate(data.to_pylist(), 1):
            profile["rows"] += 1
            geometry = original.pop(geometry_name)
            row = {k: _value(v) for k, v in original.items()}
            native_id = row["facility_id"]
            if native_id in (None, ""):
                parser.reject(path, table, number, row, "Missing Jasansky facility_id")
                continue
            children = dict(linked[native_id])
            row["_linked_tables"] = children
            source_ids = {row.get("source_id")}
            for records in children.values():
                source_ids.update(r["raw_fields"].get("source_id") for r in records)
            lon, lat = _point_wkb(geometry)
            yield parser.record(
                path,
                table,
                number,
                row,
                native_id=native_id,
                name=row["facility_name"],
                kind="source_group"
                if row["facility_type"] in ("Region", "Company")
                else "facility",
                lon=lon,
                lat=lat,
                country=row["country"],
                commodities=("primary_commodity", "commodities_products"),
                statuses=("activity_status", "activity_status_year"),
                sizes=("surface_area_sq_km", "concession_area_sq_km"),
                aliases=(row.get("facility_other_names"), row.get("sub_site_other_names")),
                reference={
                    "source_id": row.get("source_id"),
                    "source_records": [
                        references[s] for s in sorted(source_ids - {None, ""}) if s in references
                    ],
                    "support_tables": sorted(children),
                    "source_table": "data/source_ids.csv",
                    "source_file": parser.file(path),
                    "crs": metadata["crs"],
                },
                geometry=geometry,
            )


PARSERS = {
    "gseu_crm": _gseu,
    "ga_global_ree": _ga_global,
    "ga_critical_2025": _ga_workbook,
    "ga_critical_2025_arcgis": _arcgis,
    "icmm": _icmm,
    "wapha": _wapha,
    "jasansky": _jasansky,
}


def parse(root: Path, source_id: str) -> dict:
    """Return a single-use row stream plus mutable accounting finalized on exhaustion."""
    if source_id not in PARSERS:
        raise ValueError(f"Unsupported facility/deposit source: {source_id}")
    root = Path(root)
    parser = _Parse(root, source_id)
    directory = _snapshot(parser.raw, source_id)
    return {
        "rows": PARSERS[source_id](parser, directory),
        "tables": parser.tables,
        "notes": parser.notes,
        "rejects": parser.rejects,
    }
