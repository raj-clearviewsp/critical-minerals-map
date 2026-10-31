"""Read source vocabularies without normalizing records or altering raw snapshots.

Each observation preserves a complete source cell, including punctuation and
whitespace. Tokenization belongs to a field-specific resolver, not this audit.
"""

from __future__ import annotations

import csv
import io
import json
import tempfile
import zipfile
from collections import Counter
from pathlib import Path, PurePosixPath

import pyogrio

REE_FIELDS = {
    "Commods": "commodity",
    "Dep_Type": "deposit_type",
    "Status": "status",
    "P_Status": "status",
    "Country": "country",
}
GLOBAL_FIELDS = {
    "CRITICAL_MINERAL": "commodity",
    "DEPOSIT_TYPE": "deposit_type",
    "LOCATION": "country",
}
US_FIELDS = {"critmin": "commodity", "deptype": "deposit_type", "depcat": "status"}
LATERITE_FIELDS = {
    "Status": "status",
    "Subtype": "deposit_type",
    "Country": "country",
    "CountryCode": "country",
}
JASANSKY_FIELDS = {
    "primary_commodity": "commodity",
    "commodities_products": "commodity",
    "activity_status": "status",
    "country": "country",
    "GID_0": "country",
}
MRDS_FIELDS = {
    "MRDS.txt": {"code_list": "commodity", "dev_stat": "status"},
    "Commodity.txt": {"code": "commodity", "commod": "commodity"},
    "Place.txt": {"fips": "country"},
}


class _Audit:
    def __init__(self, root: Path):
        self.root = root
        self.observations: list[dict] = []
        self.tables: list[dict] = []
        self.notes: list[str] = []
        self.lookups: list[dict] = []

    def table(
        self,
        source_id: str,
        path: Path,
        name: str,
        rows,
        fields: list[str],
        policy: dict[str, str],
        *,
        id_field: str | None = None,
        encoding: str | None = None,
    ) -> None:
        if missing := set(policy) - set(fields):
            raise ValueError(f"{source_id}/{name}: missing vocabulary fields {sorted(missing)}")
        identity = {
            "source_id": source_id,
            "file": path.relative_to(self.root / "data/raw").as_posix(),
            "table": name,
        }
        counts: dict[str, Counter] = {field: Counter() for field in policy}
        examples: dict[tuple[str, str], list[dict]] = {}
        profiles = {field: {"null": 0, "blank": 0, "nonblank": 0} for field in policy}
        count = 0
        for count, row in enumerate(rows, start=1):
            if None in row:
                raise ValueError(f"{source_id}/{name}: malformed row {count}")
            for field in policy:
                value = row[field]
                if value is None:
                    profiles[field]["null"] += 1
                    continue
                text = str(value)
                if not text.strip():
                    profiles[field]["blank"] += 1
                    continue
                profiles[field]["nonblank"] += 1
                counts[field][text] += 1
                sample = examples.setdefault((field, text), [])
                if len(sample) < 3:
                    reference = {"row": count}
                    if id_field:
                        reference["id_field"] = id_field
                        reference["id"] = row[id_field]
                    sample.append(reference)
        for field, values in counts.items():
            for value, number in values.items():
                self.observations.append(
                    {
                        **identity,
                        "field": field,
                        "kind": policy[field],
                        "raw_value": value,
                        "count": number,
                        "row_examples": examples[(field, value)],
                    }
                )
        profile = {**identity, "rows": count, "fields": fields, "field_profiles": profiles}
        if encoding:
            profile["encoding"] = encoding
        self.tables.append(profile)

    def result(self) -> dict:
        order = lambda item: tuple(  # noqa: E731
            str(item.get(key, ""))
            for key in ("source_id", "file", "table", "kind", "field", "raw_value")
        )
        return {
            "observations": sorted(self.observations, key=order),
            "tables": sorted(self.tables, key=order),
            "notes": self.notes,
            "lookups": self.lookups,
        }


def _snapshot(root: Path, source_id: str) -> tuple[Path, dict] | None:
    directories = sorted((root / "data/raw" / source_id).glob("????-??-??"))
    if not directories:
        return None
    path = directories[-1]
    return path, json.loads((path / "manifest.json").read_text())


def _file(snapshot: tuple[Path, dict], suffix: str) -> Path:
    path, manifest = snapshot
    files = [path / item["path"] for item in manifest["files"] if item["path"].endswith(suffix)]
    if len(files) != 1:
        raise ValueError(f"Expected one {suffix} input in {path.name}")
    return files[0]


def _extract_archive(path: Path, destination: Path) -> None:
    with zipfile.ZipFile(path) as archive:
        if sum(item.file_size for item in archive.infolist()) > 512 * 1024 * 1024:
            raise ValueError("Geology archive exceeds the expanded-size bound")
        for item in archive.infolist():
            name = PurePosixPath(item.filename)
            if name.is_absolute() or ".." in name.parts or "\\" in item.filename:
                raise ValueError("Unsafe archive member path")
            if (item.external_attr >> 16) & 0o170000 == 0o120000:
                raise ValueError("Archive symlinks are not supported")
        archive.extractall(destination)


def _geodatabase(audit: _Audit, source_id: str, policies: dict[str, dict[str, str]]) -> None:
    snapshot = _snapshot(audit.root, source_id)
    if not snapshot:
        return
    archive = _file(snapshot, ".zip")
    with tempfile.TemporaryDirectory(prefix="atlas-vocab-gdb-") as temp:
        _extract_archive(archive, Path(temp))
        databases = list(Path(temp).rglob("*.gdb"))
        if len(databases) != 1:
            raise ValueError(f"{source_id}: expected exactly one geodatabase")
        database = databases[0]
        layers = {str(layer): geometry for layer, geometry in pyogrio.list_layers(database)}
        if missing := set(policies) - layers.keys():
            raise ValueError(f"{source_id}: missing layers {sorted(missing)}")
        for layer, geometry in layers.items():
            info = pyogrio.read_info(database, layer=layer, force_feature_count=True)
            if layer in policies:
                _, table = pyogrio.read_arrow(database, layer=layer, read_geometry=False)
                id_field = "ID_No" if "ID_No" in table.column_names else "DEPOSIT_NAME"
                audit.table(
                    source_id,
                    archive,
                    layer,
                    table.to_pylist(),
                    table.column_names,
                    policies[layer],
                    id_field=id_field,
                    encoding="UTF-8",
                )
                audit.tables[-1]["geometry_type"] = geometry
            else:
                audit.tables.append(
                    {
                        "source_id": source_id,
                        "file": archive.relative_to(audit.root / "data/raw").as_posix(),
                        "table": layer,
                        "rows": int(info["features"]),
                        "fields": list(info["fields"]),
                        "geometry_type": geometry,
                        "vocabulary_scope": "supporting table; no deposit observations emitted",
                    }
                )
            if layer == "Abbreviations_table":
                _, table = pyogrio.read_arrow(database, layer=layer, read_geometry=False)
                audit.lookups.append(
                    {
                        "source_id": source_id,
                        "file": archive.relative_to(audit.root / "data/raw").as_posix(),
                        "table": layer,
                        "rows": table.to_pylist(),
                        "note": "Includes units; not every abbreviation is a commodity.",
                    }
                )


def _us_critical(audit: _Audit) -> None:
    source_id = "usgs_us_critical"
    snapshot = _snapshot(audit.root, source_id)
    if not snapshot:
        return
    directory, manifest = snapshot
    for item in manifest["files"]:
        if not item["path"].startswith("page_"):
            continue
        path = directory / item["path"]
        page = json.loads(path.read_text())
        rows = [feature["attributes"] for feature in page["features"]]
        audit.table(
            source_id,
            path,
            "deposits",
            rows,
            [field["name"] for field in page["fields"]],
            US_FIELDS,
            id_field=page["objectIdFieldName"],
        )


def _laterites(audit: _Audit) -> None:
    source_id = "usgs_laterites"
    snapshot = _snapshot(audit.root, source_id)
    if not snapshot:
        return
    path = _file(snapshot, ".csv")
    # This original uses classic-Mac byte mappings and CR line endings. Decoding
    # as cp1252 fails at 0x90; Latin-1 corrupts names such as Goiânia and operators.
    with path.open(encoding="mac_roman", newline="") as stream:
        reader = csv.DictReader(stream)
        fields = reader.fieldnames
        rows = list(reader)
        audit.table(
            source_id,
            path,
            "laterites",
            rows,
            fields,
            LATERITE_FIELDS,
            id_field="NameDeposit",
            encoding="mac_roman",
        )
    pairs = Counter((row["CountryCode"], row["Country"]) for row in rows)
    audit.lookups.append(
        {
            "source_id": source_id,
            "file": path.relative_to(audit.root / "data/raw").as_posix(),
            "table": "laterites",
            "rows": [
                {"CountryCode": code, "Country": country, "count": count}
                for (code, country), count in sorted(pairs.items())
            ],
            "note": "Observed source code/name pairs, not independently validated ISO mappings.",
        }
    )
    for field in fields:
        if field.startswith(("Ni ", "Co ", "Nire ")) and "grade" in field.lower():
            audit.observations.append(
                {
                    "source_id": source_id,
                    "file": path.relative_to(audit.root / "data/raw").as_posix(),
                    "table": "laterites",
                    "field": field,
                    "kind": "commodity",
                    "raw_value": field,
                    "count": 1,
                    "value_origin": "column_header",
                    "row_examples": [{"row": "header"}],
                }
            )


def _mrds(audit: _Audit) -> None:
    source_id = "usgs_mrds"
    snapshot = _snapshot(audit.root, source_id)
    if not snapshot:
        return
    path = _file(snapshot, ".zip")
    code_names: Counter = Counter()

    def record_code_names(rows):
        for row in rows:
            code_names[(row["code"], row["commod"])] += 1
            yield row

    with zipfile.ZipFile(path) as archive:
        for name in sorted(archive.namelist()):
            if not name.endswith(".txt"):
                continue
            with (
                archive.open(name) as member,
                io.TextIOWrapper(member, encoding="utf-8-sig", newline="") as stream,
            ):
                reader = csv.DictReader(stream, delimiter="\t")
                fields = reader.fieldnames
                audit.table(
                    source_id,
                    path,
                    name,
                    record_code_names(reader) if name == "Commodity.txt" else reader,
                    fields,
                    MRDS_FIELDS.get(name, {}),
                    id_field="dep_id" if "dep_id" in fields else None,
                    encoding="UTF-8",
                )
    audit.lookups.append(
        {
            "source_id": source_id,
            "file": path.relative_to(audit.root / "data/raw").as_posix(),
            "table": "Commodity.txt",
            "rows": [
                {"code": code, "commod": name, "count": count}
                for (code, name), count in sorted(code_names.items())
            ],
            "note": "Observed code/name pairs; comma modifiers in names remain intact.",
        }
    )


def _jasansky(audit: _Audit) -> None:
    source_id = "jasansky"
    snapshot = _snapshot(audit.root, source_id)
    if not snapshot:
        return
    path = _file(snapshot, ".zip")
    with tempfile.TemporaryDirectory(prefix="atlas-vocab-gpkg-") as temp:
        with zipfile.ZipFile(path) as archive:
            packages = [name for name in archive.namelist() if name.endswith(".gpkg")]
            if len(packages) != 1:
                raise ValueError("Jasansky archive must contain exactly one GeoPackage")
            member = packages[0]
            gpkg = Path(temp) / "facilities.gpkg"
            gpkg.write_bytes(archive.read(member))
        _, table = pyogrio.read_arrow(gpkg, layer="facilities", read_geometry=False)
        audit.table(
            source_id,
            path,
            member + ":facilities",
            table.to_pylist(),
            table.column_names,
            JASANSKY_FIELDS,
            id_field="facility_id",
            encoding="UTF-8",
        )


def extract(root: Path) -> dict:
    """Return aggregate source cells, table accounting and field-specific caveats.

    The caller verifies manifests before invoking this read-only audit. Missing
    source snapshots emit no observations; present snapshots must match their
    registered format. No records, geometries or map points are normalized here.
    """
    audit = _Audit(Path(root))
    _geodatabase(
        audit,
        "usgs_global_ree",
        {"Global_REE": REE_FIELDS, "Global_REE_nonspatial_table": REE_FIELDS},
    )
    _geodatabase(
        audit,
        "usgs_global_critical",
        {"PP1802_CritMin_pts": GLOBAL_FIELDS, "PP1802_CritMin_polys": GLOBAL_FIELDS},
    )
    _us_critical(audit)
    _laterites(audit)
    _mrds(audit)
    _jasansky(audit)
    audit.notes.extend(
        [
            "All raw values are whole cells. No commodity, deposit-type or status tokenization "
            "occurs here. Row examples use 1-based data-record order, excluding header rows.",
            "USGS REE located and nonspatial tables are separately audited. Country contains "
            "trailing spaces and historical names. Status and P_Status represent different "
            "concepts; a geological Deposit or Occurrence is not operating status.",
            "USGS REE supporting boundary, reference and abbreviation tables are counted "
            "separately and are not additional mineral records. Abbreviations include units.",
            "USGS global critical LOCATION is the original geographic label, not guaranteed "
            "to be a current ISO country. Point and polygon layers remain distinct.",
            "USGS U.S. critmin is a comma-delimited commodity list. depcat is a historical "
            "producer/resource category, not evidence of current operation. minsystem is "
            "a separate mineral-system classification, not a deposit-type synonym.",
            "Laterites CSV is MacRoman with CR newlines and quoted embedded CR. CountryCode "
            "uses non-ISO source codes. Ni/Co grade-header observations count headers once "
            "and do not assert that every row contains a meaningful cobalt estimate.",
            "MRDS code_list is space-delimited, as documented by About.txt. Commodity.code "
            "and Commodity.commod are separate code/name evidence. Names.status describes "
            "name history, not mine status. Materials.material is a mineral assemblage, "
            "not an independently inferred commodity.",
            "MRDS Place.fips is a prefixed FIPS place identifier, not ISO: numeric codes "
            "include U.S. subnational units and alphabetic codes include countries. "
            "No spatial or country inference is made. Child-table row counts need not equal "
            "MRDS.txt; foreign-key reconciliation belongs to normalization.",
            "Jasansky facilities preserve facility and sub-site identity. Country and GID_0 "
            "are separate source fields. Facility_type is not a geological deposit type. "
            "Activity_status is recorded only where populated; production dates do not "
            "supply an inferred operating status.",
        ]
    )
    return audit.result()
