"""Lossless geological source adapters for staging, without pilot filtering."""

from __future__ import annotations

import csv
import hashlib
import io
import json
import math
import sqlite3
import tempfile
import zipfile
from collections import Counter, defaultdict
from pathlib import Path

import pyogrio

from pipeline.vocab.geology import _extract_archive, _file, _snapshot


def _relative(root: Path, path: Path) -> str:
    return path.relative_to(root / "data/raw").as_posix()


def _identity(value) -> str:
    if value is None or not str(value).strip():
        raise ValueError("Missing source native identifier")
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("Nonfinite source native identifier")
        if value.is_integer():
            return str(int(value))
    return str(value)


def _coordinate(value):
    """Convert numeric strings only; the common writer handles invalid raw cells."""
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return value


def _alias(value) -> list[str]:
    # A free-text alias cell is kept whole unless the source provides child rows.
    return [str(value)] if value is not None and str(value).strip() else []


def _record(
    native_id: str,
    file: str,
    table: str,
    number: int,
    row: dict,
    *,
    name,
    lat=None,
    lon=None,
    country=None,
    unlocated=False,
) -> dict:
    latitude, longitude = (None, None) if unlocated else (_coordinate(lat), _coordinate(lon))
    return {
        "native_id": native_id,
        "source_file": file,
        "source_table": table,
        "row_number": number,
        "record_kind": "deposit",
        "raw_name": name,
        "aliases": [],
        "lat": latitude,
        "lon": longitude,
        "location_precision": "none" if latitude is None and longitude is None else None,
        "country": country,
        "raw_commodities": {},
        "raw_deposit_type": {},
        "raw_status": {},
        "raw_size_grade_text": {},
        "reference": {},
        "raw_fields": dict(row),
    }


def _unique(rows):
    seen = set()
    for row in rows:
        identifier = row["native_id"]
        if identifier in seen:
            raise ValueError(f"Duplicate source native ID: {identifier}")
        seen.add(identifier)
        yield row


def _geo_rows(table):
    for batch in table.to_batches(max_chunksize=256):
        yield from batch.to_pylist()


def _gdb(root: Path, source_id: str) -> dict:
    snapshot = _snapshot(root, source_id)
    if snapshot is None:
        raise ValueError(f"Missing raw snapshot: {source_id}")
    archive = _file(snapshot, ".zip")
    file = _relative(root, archive)
    ree = source_id == "usgs_global_ree"
    wanted = (
        {"Global_REE", "Global_REE_nonspatial_table"}
        if ree
        else {"PP1802_CritMin_pts", "PP1802_CritMin_polys"}
    )
    primary = []
    tables = []
    references: dict[str, list[dict]] = defaultdict(list)
    with tempfile.TemporaryDirectory(prefix="atlas-normalize-gdb-") as temp:
        _extract_archive(archive, Path(temp))
        databases = list(Path(temp).rglob("*.gdb"))
        if len(databases) != 1:
            raise ValueError(f"{source_id}: expected exactly one geodatabase")
        database = databases[0]
        layers = {str(layer): geometry for layer, geometry in pyogrio.list_layers(database)}
        if missing := wanted - layers.keys():
            raise ValueError(f"{source_id}: missing required tables {sorted(missing)}")
        for name, geometry in layers.items():
            info = pyogrio.read_info(database, layer=name, force_feature_count=True)
            role = "primary" if name in wanted else "support"
            if name == "All_Global_REE_references":
                role = "reference"
                _, reference_table = pyogrio.read_arrow(
                    database, layer=name, read_geometry=False, return_fids=True
                )
                for number, row in enumerate(_geo_rows(reference_table), start=1):
                    references[str(row["short_ref"]).strip()].append(
                        {"source_file": file, "source_table": name, "row_number": number, **row}
                    )
            tables.append(
                {
                    "file": file,
                    "table": name,
                    "rows": int(info["features"]),
                    "role": role,
                    "fields": list(info["fields"]),
                    "geometry_type": geometry,
                }
            )
            if name in wanted:
                metadata, data = pyogrio.read_arrow(database, layer=name, return_fids=True)
                if metadata["crs"] not in (None, "EPSG:4326"):
                    raise ValueError(f"Unreviewed geographic CRS: {metadata['crs']}")
                primary.append((name, metadata, data))
                tables[-1]["native_id_field"] = metadata["fid_column"]
                if ree:
                    counts = Counter(data.column("ID_No").to_pylist())
                    tables[-1]["duplicate_business_ids"] = [
                        {"ID_No": identifier, "rows": count}
                        for identifier, count in sorted(counts.items())
                        if count > 1
                    ]
    for item in snapshot[1]["files"]:
        if item["path"].endswith(".xml"):
            tables.append(
                {
                    "file": _relative(root, snapshot[0] / item["path"]),
                    "table": item["path"],
                    "rows": 1,
                    "role": "reference",
                    "row_unit": "metadata document",
                    "fields": [],
                }
            )

    def rows():
        for name, metadata, data in primary:
            for number, original in enumerate(_geo_rows(data), start=1):
                row = dict(original)
                geometry = row.pop(metadata["geometry_name"], None)
                if ree:
                    unlocated = name == "Global_REE_nonspatial_table"
                    record = _record(
                        f"{name}:{_identity(row[metadata['fid_column']])}",
                        file,
                        name,
                        number,
                        row,
                        name=row["Name"],
                        lat=row["Latitude"],
                        lon=row["Longitude"],
                        country=row["Country"],
                        unlocated=unlocated,
                    )
                    record["aliases"] = _alias(row.get("Name_Other"))
                    record["raw_commodities"] = {"Commods": row["Commods"]}
                    record["raw_deposit_type"] = {
                        key: row[key] for key in ("Dep_Type", "Dep_Note", "Dep_Form", "Rec_Type")
                    }
                    record["raw_status"] = {
                        key: row[key] for key in ("Status", "Stat_Note", "P_Status", "PStat_Note")
                    }
                    record["raw_size_grade_text"] = {
                        key: value
                        for key, value in row.items()
                        if key.startswith("RR_")
                        or key in ("REE", "HREE_Note", "LREE_Note", "REE_Ratio", "P_Note")
                    }
                    refs = {
                        key: value
                        for key, value in row.items()
                        if key.casefold() in ("ref_list", "p_refs", "rr_refs")
                    }
                    linked = []
                    unmatched = []
                    requested = dict.fromkeys(
                        token.strip()
                        for value in refs.values()
                        if isinstance(value, str)
                        for token in value.split(";")
                        if token.strip()
                    )
                    for short in requested:
                        if short in references:
                            linked.extend(references[short])
                        else:
                            unmatched.append(short)
                    record["reference"] = {
                        **refs,
                        "linked_reference_rows": linked,
                        "unmatched_citation_text": unmatched,
                    }
                    if unlocated:
                        record["raw_fields"]["parser_note"] = (
                            "Source nonspatial table retained off-map; original coordinates, "
                            "if any, remain in raw_fields."
                        )
                else:
                    record = _record(
                        f"{name}:{_identity(row[metadata['fid_column']])}",
                        file,
                        name,
                        number,
                        row,
                        name=row["DEPOSIT_NAME"],
                        lat=row["LATITUDE"],
                        lon=row["LONGITUDE"],
                        country=row["LOCATION"],
                    )
                    record["raw_commodities"] = {"CRITICAL_MINERAL": row["CRITICAL_MINERAL"]}
                    record["raw_deposit_type"] = {"DEPOSIT_TYPE": row["DEPOSIT_TYPE"]}
                    record["reference"] = {
                        "doi": "10.5066/F7GH9GQR",
                        "metadata_files": [t["file"] for t in tables if t["role"] == "reference"],
                    }
                if geometry is not None:
                    record["raw_geometry"] = geometry.hex()
                yield record

    return {
        "rows": rows(),
        "tables": tables,
        "rejects": [],
        "notes": [
            "GDB source coordinates and WKB geometry are retained without precision inference. "
            "Polygon latitude/longitude fields are publisher-supplied; no centroid is computed.",
            "REE nonspatial records are retained with no map coordinates. Reference joins use "
            "exact semicolon-delimited citation labels; unmatched citation text remains intact.",
            "Native GDB OBJECTID with table prefix identifies each record. REE ID_No is kept "
            "as a source business identifier because it is not unique in the located table; "
            "duplicate-business-ID groups are reported without merging or discarding rows.",
        ],
    }


def _us(root: Path) -> dict:
    snapshot = _snapshot(root, "usgs_us_critical")
    if snapshot is None:
        raise ValueError("Missing raw snapshot: usgs_us_critical")
    directory, manifest = snapshot
    tables, pages = [], []
    expected, actual = None, []
    for item in manifest["files"]:
        path = directory / item["path"]
        value = json.loads(path.read_text())
        file = _relative(root, path)
        if item["path"].startswith("page_"):
            features = value["features"]
            pages.append((file, features))
            actual.extend(f["attributes"][value["objectIdFieldName"]] for f in features)
            tables.append(
                {"file": file, "table": "deposits", "rows": len(features), "role": "primary"}
            )
        else:
            count = 1
            if "objectIds" in value:
                expected = value["objectIds"]
                count = len(expected)
            tables.append({"file": file, "table": item["path"], "rows": count, "role": "support"})
    if len(actual) != len(set(actual)):
        raise ValueError("Duplicate U.S. critical source object IDs")
    if expected is not None and set(actual) != set(expected):
        raise ValueError("U.S. critical pages do not reconcile with the raw object-ID list")

    def rows():
        for file, features in pages:
            for number, feature in enumerate(features, start=1):
                row = feature["attributes"]
                record = _record(
                    f"deposits:{_identity(row['fid'])}",
                    file,
                    "deposits",
                    number,
                    row,
                    name=row["deposit_na"],
                    lat=row["lat_wgs84"],
                    lon=row["long_wgs84"],
                )
                record["raw_commodities"] = {"critmin": row["critmin"]}
                record["raw_deposit_type"] = {key: row[key] for key in ("deptype", "minsystem")}
                record["raw_status"] = {"depcat": row["depcat"]}
                record["raw_size_grade_text"] = {
                    key: value
                    for key, value in row.items()
                    if key.startswith(("prduction", "resources")) or key == "rank"
                }
                record["reference"] = {key: row.get(key) for key in ("references_", "links")}
                if feature.get("geometry") is not None:
                    geometry = feature["geometry"]
                    record["raw_fields"]["source_geometry"] = geometry
                    record["raw_geometry"] = {
                        "type": "Point",
                        "coordinates": [geometry["x"], geometry["y"]],
                    }
                yield record

    return {
        "rows": rows(),
        "tables": tables,
        "rejects": [],
        "notes": [
            "Original ranking, mineral-system, producer category and production/resource text "
            "are retained. No maturity or production measure is inferred.",
            "The source has state labels but no country column; country remains null rather "
            "than assigning every U.S.-scope territory an inferred ISO jurisdiction.",
        ],
    }


def _laterites(root: Path) -> dict:
    snapshot = _snapshot(root, "usgs_laterites")
    if snapshot is None:
        raise ValueError("Missing raw snapshot: usgs_laterites")
    path = _file(snapshot, ".csv")
    file = _relative(root, path)
    with path.open("rb") as stream:
        source_sha256 = hashlib.file_digest(stream, "sha256").hexdigest()
    with path.open(encoding="mac_roman", newline="") as stream:
        reader = csv.DictReader(stream)
        source_rows = list(reader)
        fields = reader.fieldnames
    if any(None in row or any(v is None for v in row.values()) for row in source_rows):
        raise ValueError("Malformed laterites CSV row")
    grade_fields = [
        key for key in fields if key.startswith(("Ni ", "Co ", "Nire ")) and "grade" in key.lower()
    ]

    def rows():
        for number, row in enumerate(source_rows, start=1):
            record = _record(
                f"laterites:{source_sha256}:{number}",
                file,
                "laterites",
                number,
                row,
                name=row["NameDeposit"],
                lat=row["LatitudeDecimal"],
                lon=row["LongitudeDecimal"],
                country=row["Country"],
            )
            record["aliases"] = _alias(row.get("OtherNames"))
            record["raw_commodities"] = {key: row[key] for key in grade_fields}
            record["raw_deposit_type"] = {"Subtype": row["Subtype"]}
            record["raw_status"] = {"Status": row["Status"], "StartupDate": row["StartupDate"]}
            record["raw_size_grade_text"] = {
                key: value
                for key, value in row.items()
                if any(part in key.lower() for part in ("grade", "tonnage", "standard", "cutoff"))
                or key.startswith(("Deposit", "LateriteThick"))
            }
            record["reference"] = {key: row[key] for key in ("References", "Source TonnGrade")}
            yield record

    return {
        "rows": rows(),
        "tables": [
            {
                "file": file,
                "table": "laterites",
                "role": "primary",
                "rows": len(source_rows),
                "fields": fields,
                "encoding": "mac_roman",
                "identity_basis": "full source-file SHA-256 and 1-based source record order",
                "source_sha256": source_sha256,
            }
        ],
        "rejects": [],
        "notes": [
            "Source has no native record key; the fallback ID combines the table name, full "
            "source-file SHA-256, and 1-based source record order. Revised or reordered file "
            "bytes receive distinct IDs. Alias cells are not generically split.",
            "MacRoman decoding and quoted CR line endings preserve accented names and grade "
            "inequalities. Numeric grades stay raw text; zero/blank grades do not prove cobalt "
            "inclusion. Publisher-truncated ##### text is preserved, not reconstructed.",
        ],
    }


def _zip_rows(archive: zipfile.ZipFile, name: str):
    with (
        archive.open(name) as member,
        io.TextIOWrapper(member, encoding="utf-8-sig", newline="") as stream,
    ):
        reader = csv.DictReader(stream, delimiter="\t")
        for number, row in enumerate(reader, start=1):
            if None in row or any(value is None for value in row.values()):
                raise ValueError(f"Malformed {name} record {number}")
            yield number, row


def _mrds(root: Path) -> dict:
    snapshot = _snapshot(root, "usgs_mrds")
    if snapshot is None:
        raise ValueError("Missing raw snapshot: usgs_mrds")
    path = _file(snapshot, ".zip")
    file = _relative(root, path)
    tables, primary_ids = [], set()
    accession = []
    child_names = ("Names.txt", "Commodity.txt", "Materials.txt", "Place.txt", "Dups.txt")
    with zipfile.ZipFile(path) as archive:
        for _, row in _zip_rows(archive, "MRDS.txt"):
            identity = _identity(row["dep_id"])
            if identity in primary_ids:
                raise ValueError(f"Duplicate source native ID: MRDS:{identity}")
            primary_ids.add(identity)
        for name in sorted(n for n in archive.namelist() if n.endswith(".txt")):
            count, linked, fields = 0, 0, []
            orphan_examples = []
            for count, row in _zip_rows(archive, name):
                if not fields:
                    fields = list(row)
                if name in child_names:
                    if row["dep_id"] in primary_ids:
                        linked += 1
                    elif len(orphan_examples) < 5:
                        orphan_examples.append({"row_number": count, "dep_id": row["dep_id"]})
                if name == "Accession.txt":
                    accession.append({"row_number": count, **row})
            profile = {
                "file": file,
                "table": name,
                "rows": count,
                "role": "primary" if name == "MRDS.txt" else "support",
                "fields": fields,
            }
            if name in child_names:
                profile.update(
                    linked_rows=linked,
                    unmatched_parent_rows=count - linked,
                    unmatched_parent_examples=orphan_examples,
                )
            tables.append(profile)
        for name in ("mrds.met", "schema.ini"):
            if name in archive.namelist():
                tables.append(
                    {
                        "file": file,
                        "table": name,
                        "rows": 1,
                        "role": "reference",
                        "row_unit": "metadata document",
                        "fields": [],
                    }
                )
    del primary_ids

    def rows():
        # The child payloads exceed a million rows. A temporary indexed database
        # bounds resident memory and retains every field and source row locator.
        with tempfile.TemporaryDirectory(prefix="atlas-normalize-mrds-") as temp:
            connection = sqlite3.connect(str(Path(temp) / "children.sqlite"))
            try:
                connection.execute(
                    "CREATE TABLE children (dep_id TEXT, source_table TEXT, row_number INTEGER, "
                    "payload TEXT)"
                )
                with zipfile.ZipFile(path) as archive:
                    for name in child_names:
                        connection.executemany(
                            "INSERT INTO children VALUES (?, ?, ?, ?)",
                            (
                                (row["dep_id"], name, number, json.dumps(row, ensure_ascii=False))
                                for number, row in _zip_rows(archive, name)
                            ),
                        )
                    connection.execute("CREATE INDEX child_parent ON children (dep_id)")
                    connection.commit()
                    for number, row in _zip_rows(archive, "MRDS.txt"):
                        linked = {name: [] for name in child_names}
                        for table, child_number, payload in connection.execute(
                            "SELECT source_table, row_number, payload FROM children "
                            "WHERE dep_id=? ORDER BY source_table, row_number",
                            (row["dep_id"],),
                        ):
                            linked[table].append(
                                {"row_number": child_number, "values": json.loads(payload)}
                            )
                        record = _record(
                            f"MRDS:{_identity(row['dep_id'])}",
                            file,
                            "MRDS.txt",
                            number,
                            row,
                            name=row["name"],
                            lat=row["latitude"],
                            lon=row["longitude"],
                        )
                        if row["dev_stat"] == "Plant":
                            record["record_kind"] = "facility"
                        record["aliases"] = list(
                            dict.fromkeys(
                                item["values"]["name"]
                                for item in linked["Names.txt"]
                                if item["values"]["name"].strip()
                            )
                        )
                        record["raw_commodities"] = {
                            "code_list": row["code_list"],
                            "Commodity.txt": [item["values"] for item in linked["Commodity.txt"]],
                        }
                        record["raw_status"] = {"dev_stat": row["dev_stat"]}
                        record["reference"] = {
                            "record_url": row["url"],
                            "source_accession": accession,
                            "metadata_member": "mrds.met",
                        }
                        record["raw_fields"]["linked_tables"] = linked
                        yield record
            finally:
                connection.close()

    primary_count = next(table["rows"] for table in tables if table["table"] == "MRDS.txt")
    place_count = next(table["rows"] for table in tables if table["table"] == "Place.txt")
    return {
        "rows": rows(),
        "tables": tables,
        "rejects": [],
        "notes": [
            f"The compact export has {primary_count:,} main records "
            f"and {place_count:,} Place rows; these are "
            "different raw table counts, not silently dropped primary records. Per-child "
            "linked/unmatched-parent counts explain reconciliation; unmatched support rows "
            "remain in the immutable archive.",
            "Names, Commodity, Materials, Place and Dups are joined by exact dep_id and retained "
            "with original child row numbers. Dups remains source evidence, not Atlas linkage.",
            "Place.fips is not a country name or ISO code, so country remains null. Compact "
            "export lacks detailed reserve/production/reference tables; source record URLs "
            "and export Accession metadata retain that provenance boundary.",
            "Plant is an explicit facility record kind; Producer/Past Producer are historical "
            "source status labels and are not converted into current operation or maturity.",
        ],
    }


def parse(root: Path, source_id: str) -> dict:
    """Return complete primary records and independently accounted support tables."""
    root = Path(root)
    if source_id in ("usgs_global_ree", "usgs_global_critical"):
        result = _gdb(root, source_id)
    elif source_id == "usgs_us_critical":
        result = _us(root)
    elif source_id == "usgs_laterites":
        result = _laterites(root)
    elif source_id == "usgs_mrds":
        result = _mrds(root)
    else:
        raise ValueError(f"No geological parser for {source_id}")
    result["rows"] = _unique(result["rows"])
    return result
