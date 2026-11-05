"""Serialize distribution-approved public projections with explicit types and spatial metadata."""

from __future__ import annotations

import csv
import gzip
import hashlib
import json
import math
import os
import struct
import subprocess
import tempfile
from collections import Counter
from datetime import date
from pathlib import Path
from urllib.parse import urlsplit

import pyarrow as pa
import pyarrow.parquet as pq
from pyproj import CRS
from shapely import from_wkb

COMMON = "source_id source_url retrieved_at evidence_grade raw_sha256 evidence"
COLUMNS = {
    "source": (
        "source_id name publisher url license redistribution_ok currency_date "
        "retrieved_at checksum checksum_basis attribution evidence_grade"
    ),
    "source_record": (
        "record_id name aliases lat lon country_iso3 commodities deposit_types "
        "raw_status location_precision"
    ),
    "site": (
        "site_id name lat lon coordinate_record_id location_precision country_iso3 "
        "commodities deposit_type maturity_rung maturity_reason status status_as_of "
        "reporting_system review_state record_ids"
    ),
    "site_member": "site_id record_id method",
    "facility": (
        "facility_id site_id name facility_type lat lon commodities status "
        "status_as_of operator_name"
    ),
    "production": (
        "entity_ref entity_type commodity year quantity unit quantity_basis production_estimated"
    ),
    "country_commodity": (
        "country_iso3 country_name commodity year production reserves unit "
        "quantity_basis world_share_pct production_qualifier reserves_qualifier scope "
        "production_estimated reserves_lower_bound reserves_raw"
    ),
    "export_restriction": (
        "country_iso3 commodity year commodity_code measure_type variation value unit "
        "start_date_raw end_date_raw value_condition"
    ),
    "china_official_finance": (
        "project_id country_iso3 commitment_year title amount currency amount_basis "
        "lender commodities site_id amount_estimated recommended_for_aggregates "
        "source_status flow_type"
    ),
}
TABLES = {k: tuple((s if k == "source" else s + " " + COMMON).split()) for k, s in COLUMNS.items()}
TITLES = {
    "source": "Sources",
    "source_record": "Source observations",
    "site": "Site entries",
    "site_member": "Site memberships",
    "facility": "Facilities",
    "production": "Production",
    "country_commodity": "Country production and reserves",
    "export_restriction": "Historical export restrictions",
    "china_official_finance": "Chinese official finance",
}
SPATIAL = {"site", "source_record", "facility"}
LISTS = {"aliases", "commodities", "deposit_types", "record_ids"}
INTEGER = {"year", "commitment_year", "maturity_rung"}
FLOAT = {
    "lat",
    "lon",
    "quantity",
    "production",
    "reserves",
    "world_share_pct",
    "amount",
    "reserves_lower_bound",
}
CITATION_FIELDS = {
    "source_id",
    "source_url",
    "retrieved_at",
    "evidence_grade",
    "raw_sha256",
    "field",
    "record_id",
    "source_row",
    "source_table",
    "locator",
    "value",
    "unit",
    "as_of",
    "effective_date",
    "source_currency",
    "source_file",
    "native_id",
    "reference_url",
    "registered_at",
    "publisher",
    "title",
    "url",
    "page",
    "section",
    "basis",
    "source_date",
}
IDENTIFIERS = {
    "source": ("source_id",),
    "source_record": ("record_id",),
    "site": ("site_id",),
    "site_member": ("record_id",),
    "facility": ("facility_id",),
}
LIMIT = 50_000_000


def canonical(value):
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    )


def public_json(value):
    """Reject review payloads even when nested under an otherwise public citation field."""
    if isinstance(value, dict):
        for k, v in value.items():
            if k not in CITATION_FIELDS:
                raise ValueError(f"Private or invalid citation key: {k}")
            public_json(v)
    elif isinstance(value, list):
        for item in value:
            public_json(item)
    elif isinstance(value, float) and not math.isfinite(value):
        raise ValueError("Nonfinite citation value")
    elif value is not None and not isinstance(value, (str, bool, int, float)):
        raise ValueError("Unsupported citation type")


def arrow_type(field):
    if field in LISTS:
        return pa.list_(pa.field("element", pa.string()))
    if field in INTEGER:
        return pa.int64()
    if field in FLOAT:
        return pa.float64()
    if field in {"redistribution_ok", "production_estimated"}:
        return pa.bool_()
    return pa.string()


def typed(field, value):
    if value is None:
        return None
    if field == "evidence":
        if not isinstance(value, (dict, list)):
            raise ValueError("Evidence must contain citation objects")
        public_json(value)
        return canonical(value)
    if field in LISTS:
        if not isinstance(value, list) or any(not isinstance(x, str) for x in value):
            raise ValueError(f"Expected text list in {field}")
    elif field in FLOAT:
        if type(value) not in (int, float) or not math.isfinite(value):
            raise ValueError(f"Expected finite number in {field}")
        return float(value)
    elif field in INTEGER:
        if type(value) is not int:
            raise ValueError(f"Expected integer in {field}")
    elif field in {"redistribution_ok", "production_estimated"}:
        if type(value) is not bool:
            raise ValueError("Expected boolean source reuse flag")
    elif not isinstance(value, str):
        raise ValueError(f"Expected text in {field}")
    if field == "maturity_rung" and value not in range(1, 6):
        raise ValueError("Invalid maturity rung")
    if field == "evidence_grade" and value not in ("A", "B", "C", "D"):
        raise ValueError("Invalid evidence grade")
    if field == "checksum_basis" and value not in {"snapshot_manifest", "document_bytes"}:
        raise ValueError("Unrecognized source checksum basis")
    if field in {"raw_sha256", "checksum"} and (
        len(value) != 64 or any(c not in "0123456789abcdef" for c in value)
    ):
        raise ValueError("Invalid SHA-256")
    return value


def point(row, table):
    x, y = row.get("lon"), row.get("lat")
    if x is None and y is None and table != "site":
        return None
    if x is None or y is None or not (-180 <= x <= 180 and -90 <= y <= 90):
        raise ValueError(f"Invalid {table} longitude/latitude")
    if row.get("location_precision") in {"country centroid", "country_centroid", "none"}:
        if table == "site":
            raise ValueError("Site cannot use centroid-only geometry")
        return None
    return struct.pack("<BIdd", 1, 1, x, y)


def prepared(table, rows):
    if not isinstance(rows, list):
        raise ValueError("Download table must be a list of rows")
    result = []
    seen = set()
    for row in rows:
        if not isinstance(row, dict) or set(row) - set(TABLES[table]):
            extra = set(row) - set(TABLES[table]) if isinstance(row, dict) else type(row)
            raise ValueError(f"Unapproved field in {table}: {extra}")
        item = {k: typed(k, row.get(k)) for k in TABLES[table]}
        if table == "source":
            if not all(
                item[k]
                for k in ("url", "retrieved_at", "evidence_grade", "checksum", "checksum_basis")
            ):
                raise ValueError(
                    "Source download needs URL, retrieval date, grade and checksum basis"
                )
            url = urlsplit(item["url"])
            if (
                url.scheme not in ("https", "http")
                or not url.hostname
                or url.username
                or url.password
            ):
                raise ValueError("Invalid source URL")
            date.fromisoformat(item["retrieved_at"][:10])
        if table in IDENTIFIERS:
            pk = tuple(item[k] for k in IDENTIFIERS[table])
            if any(not x for x in pk) or pk in seen:
                raise ValueError(f"Missing or duplicate {table} identifier")
            seen.add(pk)
        if table in SPATIAL:
            point(item, table)
        result.append(item)
    return sorted(result, key=canonical)


def validate_references(rows):
    """Validate explicit publication decisions and references inside the supplied export."""
    source_ids = {s["source_id"] for s in rows["source"] if s["redistribution_ok"] is True}
    site_ids = {s["site_id"] for s in rows["site"]}
    record_ids = {r["record_id"] for r in rows["source_record"]}
    for table, facts in rows.items():
        if table == "source":
            continue
        for fact in facts:
            if fact["source_id"] is not None and fact["source_id"] not in source_ids:
                raise ValueError(
                    f"{table} refers to a source without an explicit distribution decision"
                )
            citations = json.loads(fact["evidence"]) if fact["evidence"] else []
            for item in citations if isinstance(citations, list) else [citations]:
                if not isinstance(item, dict) or (
                    item.get("source_id") is not None and item["source_id"] not in source_ids
                ):
                    raise ValueError(f"{table} citation refers to an unapproved source")
            if fact.get("site_id") is not None and fact["site_id"] not in site_ids:
                raise ValueError(f"{table} refers to a site outside this export")
            referenced = set(fact.get("record_ids") or [])
            if fact.get("coordinate_record_id"):
                referenced.add(fact["coordinate_record_id"])
            if table == "site_member":
                referenced.add(fact["record_id"])
            if referenced - record_ids:
                raise ValueError(f"{table} refers to an observation outside this export")


def csv_value(value):
    if value is None:
        return ""
    if isinstance(value, list):
        return canonical(value)
    if isinstance(value, str) and (
        value.lstrip().startswith(("=", "+", "-", "@")) or value.startswith(("\t", "\r", "\n"))
    ):
        return "'" + value
    return value


def dump_csv(path, headers, rows):
    with path.open("w", encoding="utf-8", newline="") as out:
        writer = csv.writer(out, lineterminator="\n")
        writer.writerow(headers)
        for row in rows:
            writer.writerow([csv_value(row.get(k)) for k in headers])
    with path.open(encoding="utf-8", newline="") as src:
        reader = csv.reader(src)
        if next(reader) != list(headers) or sum(1 for r in reader if len(r) == len(headers)) != len(
            rows
        ):
            raise ValueError("CSV round-trip mismatch")


def dump_parquet(path, table, rows):
    schema = pa.schema([(k, arrow_type(k)) for k in TABLES[table]])
    data = pa.Table.from_pylist(rows, schema=schema)
    if table in SPATIAL:
        geoms = [point(r, table) for r in rows]
        meta = {
            "encoding": "WKB",
            "geometry_types": ["Point"],
            "crs": CRS.from_user_input("OGC:CRS84").to_json_dict(),
        }
        located = [r for r, g in zip(rows, geoms, strict=True) if g is not None]
        if located:
            meta["bbox"] = [
                min(r["lon"] for r in located),
                min(r["lat"] for r in located),
                max(r["lon"] for r in located),
                max(r["lat"] for r in located),
            ]
        geo = {"version": "1.1.0", "primary_column": "geometry", "columns": {"geometry": meta}}
        data = data.append_column(
            "geometry", pa.array(geoms, type=pa.binary())
        ).replace_schema_metadata({b"geo": canonical(geo).encode()})
    pq.write_table(data, path, version="2.6", compression="zstd")
    back = pq.read_table(path)
    if not data.equals(back, check_metadata=True):
        raise ValueError("Parquet round-trip mismatch")
    if table in SPATIAL:
        md = json.loads(back.schema.metadata[b"geo"])["columns"]["geometry"]
        if not CRS.from_json_dict(md["crs"]).equals(CRS.from_user_input("OGC:CRS84")):
            raise ValueError("Wrong GeoParquet CRS")
        for row, buf in zip(rows, back["geometry"].to_pylist(), strict=True):
            if buf is not None:
                geom = from_wkb(buf)
                if geom.geom_type != "Point" or (geom.x, geom.y) != (row["lon"], row["lat"]):
                    raise ValueError("GeoParquet geography differs from source point")


DEFINITIONS = {
    "attribution": "Publisher credit required for reuse.",
    "commodity": "Reported mineral family associated with this row.",
    "country_name": "Country or aggregate label supplied in the production inventory.",
    "currency": "Currency in which the stated financing amount is expressed.",
    "deposit_type": "Geological deposit classification retained for this site.",
    "deposit_types": "Mapped deposit classifications reported by this source.",
    "entity_type": "Type of reporting entity, such as country or facility.",
    "facility_type": "Reported physical activity, such as mine or processing plant.",
    "lender": "Named financing organization from the AidData contribution record.",
    "license": "Reuse terms recorded for the source.",
    "name": "Source, observation, site or facility name in the relevant table.",
    "publisher": "Organization responsible for the source dataset.",
    "status": "Operational status supported by the dated source evidence.",
    "title": "Title of the financing contribution in the source.",
    "unit": "Measurement unit as reported for this quantity.",
    "url": "Publisher landing page or data release URL.",
    "year": "Reporting year of the observation.",
    "source_id": "Registered source supplying the observation or fact.",
    "source_url": "Citation for the contributed value.",
    "retrieved_at": "Documented retrieval timestamp.",
    "evidence_grade": "Source-evidence grade A through D.",
    "raw_sha256": "SHA-256 of retained source bytes.",
    "evidence": "Public field citations encoded as JSON.",
    "checksum": "SHA-256 of the artifact identified by checksum_basis.",
    "checksum_basis": "Whether the digest covers a snapshot manifest or a retrieved document.",
    "currency_date": "Publisher’s source currency date.",
    "redistribution_ok": "Recorded redistribution decision for the named source.",
    "record_id": "Exact original source record identifier.",
    "site_id": "Stable Atlas site-entry ULID.",
    "facility_id": "Atlas facility identifier.",
    "lat": "WGS84 latitude, degrees north.",
    "lon": "WGS84 longitude, degrees east.",
    "geometry": "Point in longitude/latitude order, WKB with OGC:CRS84.",
    "country_iso3": "ISO 3166-1 alpha-3 country code.",
    "record_ids": "Source observations assigned to the site entry.",
    "coordinate_record_id": "Original observation providing the representative point.",
    "location_precision": "Publisher’s location precision, blank when unspecified.",
    "maturity_rung": "Evidence-supported rung, 1 occurrence through 5 operating.",
    "maturity_reason": "Explanation of the evidence supporting the rung.",
    "review_state": (
        "Whether the physical site entry has one provisional source or reviewed identity."
    ),
    "commodities": "Mineral families reported in the source.",
    "method": "Basis for membership in this site entry.",
    "status_as_of": "Date to which the reported status applies.",
    "reporting_system": "Resource classification system given by the publisher.",
    "world_share_pct": "Percentage of stated total world production.",
    "quantity_basis": "Material and measurement basis used by the source.",
    "production_qualifier": "Published estimate, bound, rounding or suppression qualifier.",
    "reserves_qualifier": "Published reserve estimate, bound or suppression qualifier.",
    "amount_basis": "Financial measurement basis as stated in the source.",
    "value": "Reported measure value from OECD VAL, blank when the source gives none.",
    "variation": "OECD variation code identifying this version of the policy measure.",
    "start_date_raw": "Start date wording as supplied by the historical OECD inventory.",
    "end_date_raw": "End date wording as supplied by the historical OECD inventory.",
    "value_condition": "Published condition on the OECD measure value.",
    "commodity_code": "Publisher product classification code, preserved as text.",
    "commitment_year": "Year the financial commitment was recorded.",
    "aliases": "Alternative names explicitly supported by the source.",
    "raw_status": "Original status wording from the source.",
    "production": "Production in the row’s stated unit and measurement basis.",
    "reserves": "Reserves in the row’s stated unit and measurement basis.",
    "amount": "Stated financing amount in the recorded currency and basis.",
    "quantity": "Reported output in the stated unit.",
    "project_id": "AidData financial contribution ID, separate from the Atlas site ID.",
    "amount_estimated": "AidData’s stated estimate flag, retaining blank values.",
    "recommended_for_aggregates": "AidData’s flag for including the contribution in totals.",
    "source_status": "AidData status of the contribution, including pledges and cancellations.",
    "flow_type": "AidData financing flow type for this contribution.",
    "measure_type": "Historical policy measure category.",
    "scope": "Country, world-total or other-countries reporting scope.",
    "production_estimated": "Publisher indicates an estimated production amount.",
    "reserves_lower_bound": "Strict lower bound for reported reserves, in the stated unit.",
    "reserves_raw": (
        "Source reserves cell, including withheld, unavailable and greater-than qualifiers."
    ),
    "entity_ref": "Identifier of the producing facility or country.",
    "operator_name": "Operator named in the dated source.",
}


def dictionary():
    result = []
    for tab, cols in TABLES.items():
        for col in [*cols, *(["geometry"] if tab in SPATIAL else [])]:
            desc = DEFINITIONS[col]
            result.append(
                {
                    "table": tab,
                    "column": col,
                    "type": "WKB Point" if col == "geometry" else str(arrow_type(col)),
                    "definition": desc,
                }
            )
    return result


def descriptor(path, table, fmt, rows, label, description):
    raw = path.read_bytes()
    return {
        "path": f"downloads/{path.name}",
        "label": label,
        "format": fmt,
        "table": table,
        "rows": rows,
        "sha256": hashlib.sha256(raw).hexdigest(),
        "bytes": len(raw),
        "description": description,
    }


def varint(buf, pos):
    value = 0
    for shift in range(0, 70, 7):
        if pos >= len(buf):
            break
        byte = buf[pos]
        pos += 1
        value |= (byte & 127) << shift
        if byte < 128:
            return value, pos
    raise ValueError("Invalid PMTiles/MVT integer")


def fields(buf):
    pos = 0
    while pos < len(buf):
        tag, pos = varint(buf, pos)
        wire = tag & 7
        if wire == 0:
            v, pos = varint(buf, pos)
        else:
            if wire == 2:
                size, pos = varint(buf, pos)
            elif wire in (1, 5):
                size = 8 if wire == 1 else 4
            else:
                raise ValueError("Unexpected MVT field type")
            if pos + size > len(buf):
                raise ValueError("Truncated MVT field")
            v = buf[pos : pos + size]
            pos += size
        yield tag >> 3, v


def directory(buf):
    decompressed = gzip.decompress(buf)
    n, pos = varint(decompressed, 0)
    blocks = []
    for _ in range(4):
        block = []
        for _ in range(n):
            v, pos = varint(decompressed, pos)
            block.append(v)
        blocks.append(block)
    if pos != len(decompressed):
        raise ValueError("Incomplete PMTiles directory")
    entries = []
    tile_id = 0
    for delta, run, size, raw_offset in zip(*blocks, strict=True):
        tile_id += delta
        off = (entries[-1][2] + entries[-1][3]) if entries and raw_offset == 0 else raw_offset - 1
        if off < 0:
            raise ValueError("Invalid PMTiles address")
        entries.append((tile_id, run, off, size))
    return entries


def get_world_tile(raw):
    root, root_bytes, _, _, leaf, leaf_bytes, data, data_bytes = struct.unpack_from("<8Q", raw, 8)
    buf = raw[root : root + root_bytes]
    for _ in range(4):
        entries = directory(buf)
        if not entries or entries[0][0] != 0:
            raise ValueError("Missing world tile")
        _, run, off, size = entries[0]
        if run:
            if off + size > data_bytes:
                raise ValueError("Tile outside PMTiles data section")
            chunk = raw[data + off : data + off + size]
            return gzip.decompress(chunk) if raw[98] == 2 else chunk
        if off + size > leaf_bytes:
            raise ValueError("Invalid PMTiles leaf")
        buf = raw[leaf + off : leaf + off + size]
    raise ValueError("PMTiles directory too deep")


def feature_ids(tile):
    """Decode world-tile feature tags without a second binary or an unpinned dependency."""
    found = {}
    for tag, buf in fields(tile):
        if tag != 3:
            continue
        layer = list(fields(buf))
        name = next(v.decode() for k, v in layer if k == 1)
        keys = [v.decode() for k, v in layer if k == 3]
        values = [next(iter(fields(v))) for k, v in layer if k == 4]
        if name in found:
            raise ValueError("Duplicate MVT layer")
        found[name] = []
        for k, feature in layer:
            if k != 2:
                continue
            row = dict(fields(feature))
            pos = 0
            props = {}
            if row.get(3) != 1 or not row.get(4):
                raise ValueError("Expected real MVT point geometry")
            tags = row.get(2, b"")
            while pos < len(tags):
                keyidx, pos = varint(tags, pos)
                validx, pos = varint(tags, pos)
                kind, v = values[validx]
                props[keys[keyidx]] = v.decode() if kind == 1 else v
            if not isinstance(props.get("id"), str) or set(props) - {
                "id",
                "name",
                "rung",
                "minerals",
                "country",
                "status",
                "site_id",
            }:
                raise ValueError("Unexpected map tile attributes")
            found[name].append(props["id"])
    return found


def gz(value):
    return gzip.compress(value, compresslevel=9, mtime=0)


def empty_archive():
    root = gz(b"\0")
    md = gz(
        canonical(
            {
                "name": "Critical Minerals Atlas",
                "format": "pbf",
                "generator": "Critical Minerals Atlas empty inventory",
                "vector_layers": [],
            }
        ).encode()
    )
    end = 127 + len(root) + len(md)
    h = bytearray(127)
    h[:8] = b"PMTiles\x03"
    struct.pack_into(
        "<11Q", h, 8, 127, len(root), 127 + len(root), len(md), end, 0, end, 0, 0, 0, 0
    )
    h[96:102] = bytes((1, 2, 2, 1, 0, 0))
    return bytes(h) + root + md


def declare_layers(raw, expected):
    if (
        len(raw) < 127
        or raw[:8] != b"PMTiles\x03"
        or raw[97] != 2
        or raw[98] not in (1, 2)
        or raw[99] != 1
    ):
        raise ValueError("Expected PMTiles v3 vector archive")
    root, size, off, mdsize, leaf, leafsize, data, datasize = struct.unpack_from("<8Q", raw, 8)
    if (root, off, leaf, data, len(raw)) != (
        127,
        root + size,
        off + mdsize,
        leaf + leafsize,
        data + datasize,
    ):
        raise ValueError("Invalid PMTiles section offsets")
    meta = json.loads(gzip.decompress(raw[off : off + mdsize]))
    layer_ids = {r["id"] for r in meta["vector_layers"]}
    if layer_ids - set(expected) or any(
        ids and layer not in layer_ids for layer, ids in expected.items()
    ):
        raise ValueError("PMTiles layer differs from input")
    for layer in sorted(expected.keys() - layer_ids):
        meta["vector_layers"].append(
            {
                "id": layer,
                "description": "No located, distribution-approved features in this release.",
                "minzoom": raw[100],
                "maxzoom": raw[101],
                "fields": {},
            }
        )
    meta["vector_layers"].sort(key=lambda layer: layer["id"])
    md = gz(canonical(meta).encode())
    delta = len(md) - mdsize
    h = bytearray(raw[:127])
    struct.pack_into("<Q", h, 32, len(md))
    struct.pack_into("<Q", h, 40, leaf + delta)
    struct.pack_into("<Q", h, 56, data + delta)
    result = bytes(h) + raw[127:off] + md + raw[off + mdsize :]
    if len(result) >= LIMIT:
        raise ValueError("PMTiles exceeds 50 MB release budget")
    count = struct.unpack_from("<Q", result, 72)[0]
    if any(expected.values()):
        found = feature_ids(get_world_tile(result))
        if (
            not count
            or any(Counter(ids) != Counter(found.get(layer, [])) for layer, ids in expected.items())
            or set(found) - expected.keys()
        ):
            raise ValueError("PMTiles world-tile ID reconciliation failed")
    elif count or directory(result[root : root + size]):
        raise ValueError("Empty archive contains unexpected features")
    return result


def write_tiles(work, path, rows, executable):
    by_id = {s["site_id"]: s for s in rows["site"]}
    expected = {"sites": [], "facilities": []}
    for table, layer in [("site", "sites"), ("facility", "facilities")]:
        features = []
        for row in rows[table]:
            if point(row, table) is None:
                continue
            fid = row[table + "_id"]
            expected[layer].append(fid)
            parent = by_id.get(row["site_id"], {})
            props = {
                "id": fid,
                "name": row.get("name"),
                "rung": row.get("maturity_rung"),
                "minerals": ", ".join(row.get("commodities") or []),
                "country": row.get("country_iso3") or parent.get("country_iso3"),
                "status": row.get("status"),
            }
            if table == "facility":
                props["site_id"] = row.get("site_id")
            features.append(
                {
                    "type": "Feature",
                    "geometry": {"type": "Point", "coordinates": [row["lon"], row["lat"]]},
                    "properties": {k: v for k, v in props.items() if v is not None},
                }
            )
        (work / f"{layer}.geojson").write_text(
            canonical({"type": "FeatureCollection", "features": features}) + "\n"
        )
    if any(expected.values()):
        binary = Path(executable).resolve()
        version = subprocess.run(
            [str(binary), "--version"], capture_output=True, text=True, check=True, timeout=10
        )
        if (version.stdout + version.stderr).strip() != "tippecanoe v2.79.0":
            raise ValueError("tippecanoe v2.79.0 required")
        cmd = [
            "tippecanoe",
            "-q",
            "-o",
            "points.pmtiles",
            "-n",
            "Critical Minerals Atlas",
            "-Z0",
            "-z7",
            "-B0",
            "-r1",
            "--buffer=0",
            "--no-feature-limit",
            "--no-tile-size-limit",
            "-L",
            "sites:sites.geojson",
            "-L",
            "facilities:facilities.geojson",
        ]
        subprocess.run(
            cmd,
            executable=binary,
            cwd=work,
            env={
                **os.environ,
                "LC_ALL": "C",
                "TIPPECANOE_MAX_THREADS": "1",
                "SOURCE_DATE_EPOCH": "1793750400",
            },
            capture_output=True,
            check=True,
            timeout=300,
        )
        raw = (work / "points.pmtiles").read_bytes()
    else:
        raw = empty_archive()
    path.write_bytes(declare_layers(raw, expected))
    return sum(map(len, expected.values()))


def build_downloads(tables: dict, output_dir: Path, tippecanoe: Path) -> list[dict]:
    """Build into a private temp directory, validate, then accept unchanged or new files."""
    if set(tables) != set(TABLES):
        raise ValueError("Expected all nine canonical download tables")
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    dest = out / "downloads"
    result = []
    with tempfile.TemporaryDirectory(dir=out) as temp:
        work = Path(temp)
        artifacts = work / "downloads"
        artifacts.mkdir()
        rows = {tab: prepared(tab, tables[tab]) for tab in TABLES}
        validate_references(rows)
        for tab, items in rows.items():
            title = TITLES[tab]
            c = artifacts / f"{tab}.csv"
            p = artifacts / f"{tab}.parquet"
            dump_csv(c, TABLES[tab], items)
            dump_parquet(p, tab, items)
            for f, fmt in [(c, "CSV"), (p, "GeoParquet" if tab in SPATIAL else "Parquet")]:
                result.append(
                    descriptor(
                        f,
                        tab,
                        fmt,
                        len(items),
                        f"{title} · {fmt}",
                        f"{title} approved for distribution in the November 4, 2026 release.",
                    )
                )
        dictionary_rows = dictionary()
        dd = artifacts / "data-dictionary.csv"
        dump_csv(dd, ["table", "column", "type", "definition"], dictionary_rows)
        result.append(
            descriptor(
                dd,
                "data_dictionary",
                "CSV",
                len(dictionary_rows),
                "Data dictionary",
                "Column names, types and meanings across the download tables.",
            )
        )
        tiles = artifacts / "atlas.pmtiles"
        n = write_tiles(work, tiles, rows, tippecanoe)
        result.append(
            descriptor(
                tiles,
                "map",
                "PMTiles",
                n,
                "Map tiles",
                (
                    "Located site and facility points with identifiers and source-linked site "
                    "attributes."
                ),
            )
        )
        if dest.exists():
            if dest.is_symlink() or any(
                f.is_symlink()
                or not f.is_file()
                or not (artifacts / f.name).is_file()
                or f.read_bytes() != (artifacts / f.name).read_bytes()
                for f in dest.iterdir()
            ):
                raise ValueError("Conflicting files in existing downloads directory")
            for f in artifacts.iterdir():
                if not (dest / f.name).exists():
                    os.link(f, dest / f.name)
        else:
            os.rename(artifacts, dest)
    return result
