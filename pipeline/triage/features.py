"""Source-evidenced entity scope and name roles for review, without altering staging."""

from __future__ import annotations

import json
import math
import re
from collections import defaultdict
from urllib.parse import urlsplit

from pipeline.link.candidates import haversine_km, normalize_name

ENTITY_LEVELS = frozenset({"site", "subsite", "operation_complex", "district_area", "unknown"})
GA_SOURCES = ("ga_critical_2025", "ga_critical_2025_arcgis")
GA_RELEASE = "https://doi.org/10.26186/150802"
GA_WORKBOOK = "https://d28rz98at9flks.cloudfront.net/150802/150802_01_0.xlsx"
GA_COORDINATE_TOLERANCE_KM = 0.000001
DEPOSIT_FIELDS = {
    "usgs_global_ree": {"Dep_Type"},
    "usgs_global_critical": {"DEPOSIT_TYPE"},
    "usgs_us_critical": {"deptype"},
    "usgs_laterites": {"Subtype"},
    "gseu_crm": {"Deposit type"},
}
BROAD_NAME = re.compile(
    r"\b(?:district|area|complex)\b|\b(?:about|group of|several)\s+(?:\d+\s+)?mines\b",
    re.I,
)
PROCESSING_NAME = re.compile(r"\b(?:plant|smelter|refinery|refineries)\b", re.I)
PLACEHOLDER = re.compile(r"^(?:unnamed|unknown|unidentified|unlocated)(?:\b|$)", re.I)
PROVENANCE_FIELDS = (
    "native_id",
    "source_file",
    "source_table",
    "source_row",
    "source_url",
    "raw_sha256",
    "retrieved_at",
    "registered_at",
    "source_currency",
    "evidence_grade",
)


def _json(row: dict, name: str, default):
    value = row.get(name, default)
    if value is None:
        return default
    return json.loads(value) if isinstance(value, str) else value


def _text(value) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _strings(values) -> list[str]:
    return list(dict.fromkeys(text for value in values if (text := _text(value))))


def is_placeholder_name(value) -> bool:
    """Placeholders never supply strong identity evidence; preserve their full text."""
    value = _text(value)
    if value is None:
        return True
    normalized = normalize_name(value)
    return bool(PLACEHOLDER.match(value)) or normalized in {
        "",
        "name unknown",
        "name not known",
        "not named",
        "not known",
        "no name",
        "no alternate name",
        "no recorded name",
        "not reported",
        "n a",
        "na",
        "none",
    }


def _safe_https(value) -> str | None:
    if not isinstance(value, str) or any(ord(c) <= 32 for c in value):
        return None
    try:
        parsed = urlsplit(value)
        if (
            parsed.scheme == "https"
            and parsed.hostname
            and not parsed.username
            and not parsed.password
        ):
            return value
    except ValueError:
        pass
    return None


def _evidence(field: str, value, basis: str) -> dict:
    return {
        "field": field,
        "raw_value": value,
        "basis": basis,
        "qualified": isinstance(value, str) and "?" in value,
    }


def _scope(row: dict, raw: dict) -> tuple[str, list[dict], list[str]]:
    source = row["source_id"]
    evidence, flags = [], []
    level = "unknown"
    if source == "usgs_global_ree":
        value = _text(raw.get("Rec_Type"))
        types = {
            "site": "site",
            "district or area": "district_area",
            "intrusion or complex": "operation_complex",
        }
        if value:
            level = types.get(value.replace("(?)", "").strip().casefold(), "unknown")
            evidence.append(_evidence("Rec_Type", value, "Explicit publisher record granularity"))
            if "?" in value:
                flags.append("qualified_entity_level")
            if level == "operation_complex":
                flags.append("geological_complex_not_necessarily_operating_complex")
    elif source == "jasansky":
        if _text(raw.get("sub_site_name")):
            level = "subsite"
            evidence.append(
                _evidence(
                    "sub_site_name", raw["sub_site_name"], "Explicit component of facility_name"
                )
            )
        elif raw.get("facility_type") == "Mine":
            level = "site"
            evidence.append(_evidence("facility_type", "Mine", "Explicit publisher facility type"))
    elif source in GA_SOURCES:
        field = "Status" if source == GA_SOURCES[0] else "status"
        status = raw.get(field)
        if status in {
            "Operating mine",
            "Mineral deposit",
            "Mine - care and maintenance",
            "Mine - under development",
        }:
            level = "site"
            evidence.append(
                _evidence(field, status, "Publisher explicitly identifies a mine/deposit")
            )
    elif source == "icmm":
        # ICMM About explains spatial/name clustering; multiple contributing
        # records are neither aliases nor proof of one physical complex/site.
        flags.append("publisher_cluster_record")
        evidence.append(
            _evidence(
                "Group Names",
                raw.get("Group Names"),
                "Publisher cluster membership; entity granularity remains unresolved",
            )
        )
    else:
        evidence.append(
            _evidence(
                "record_kind",
                row.get("record_kind"),
                "Normalized kind does not distinguish a physical site from a wider named area",
            )
        )
    name = _text(raw.get("sub_site_name")) or _text(row.get("raw_name")) or ""
    if BROAD_NAME.search(name):
        flags.append("name_suggests_broad_scope")
    if PROCESSING_NAME.search(name):
        flags.append("name_suggests_processing_facility")
    if level == "unknown":
        flags.append("entity_level_unresolved")
    return level, evidence, flags


def _deposit_types(source: str, raw_types: dict, vocab) -> tuple[list[str], list[dict]]:
    codes, evidence = set(), []
    for field, value in sorted(raw_types.items()):
        declared = field in DEPOSIT_FIELDS.get(source, set()) or (
            source == "ga_global_ree"
            and (field == "Deposit type" or field.startswith("Deposit type ["))
        )
        if not declared or not (text := _text(value)):
            continue
        result = vocab.resolve("deposit_type", source, field, text)
        informative = [code for code in result["codes"] if code != "unknown"]
        codes.update(informative)
        evidence.append(
            {
                "field": field,
                "raw_value": value,
                "codes": informative,
                "qualified": "?" in text,
                "unmapped": result["unmapped"],
                "resolution": result["reason"],
            }
        )
    return sorted(codes), evidence


def _source_links(row: dict, raw: dict, reference: dict) -> list[dict]:
    choices = []
    if row["source_id"] == "usgs_mrds":
        choices.append(
            (reference.get("record_url") or raw.get("url"), "Publisher record", "record")
        )
    if row["source_id"] == "usgs_us_critical":
        choices.append((raw.get("links"), "Linked source reference", "reference"))
    if row["source_id"] == "jasansky":
        for item in reference.get("source_records", []):
            choices.append(
                (item.get("source_urls"), item.get("sources") or "Cited source", "reference")
            )
    if row["source_id"] in GA_SOURCES:
        choices.append((GA_RELEASE, "Geoscience Australia release", "release"))
    choices.append((row.get("source_url"), "Original source download", "download"))
    links, seen = [], set()
    for url, label, kind in choices:
        if (url := _safe_https(url)) and url not in seen:
            links.append({"url": url, "label": label, "kind": kind})
            seen.add(url)
    return links


def enrich_record(row: dict, vocab) -> dict:
    """Derive review features, retaining raw facts and source-specific limitations."""
    raw = _json(row, "raw_fields", {})
    aliases = _strings(_json(row, "aliases", []))
    reference = _json(row, "reference", {})
    source = row["source_id"]
    original_name = row.get("raw_name")
    primary = original_name
    parent, identity, context, unverified, alias_evidence = None, [], [], list(aliases), []
    if source == "icmm":
        context = _strings(str(raw.get("Group Names") or "").split(";"))
        unverified = []
        alias_evidence.append(
            _evidence(
                "Group Names",
                raw.get("Group Names"),
                "ICMM About: contributing names from spatial/name clusters, not identity aliases",
            )
        )
    elif source == "jasansky" and _text(raw.get("sub_site_name")):
        primary, parent = raw["sub_site_name"], raw.get("facility_name")
        context = _strings([parent, raw.get("facility_other_names")])
        # A field scoped to this explicit subsite is distinct from facility-wide names.
        identity = _strings([raw.get("sub_site_other_names")])
        unverified = []
        alias_evidence.append(
            _evidence(
                "sub_site_other_names",
                raw.get("sub_site_other_names"),
                "Publisher alias field scoped to the explicit subsite; unsplit original value",
            )
        )
    else:
        if aliases:
            alias_evidence.append(
                _evidence(
                    "aliases",
                    aliases,
                    "Alternate-name field is unverified for site versus parent/area identity",
                )
            )
    if source == "usgs_global_ree":
        parent = _text(raw.get("Part_of"))
        context = _strings([*context, parent, raw.get("Components")])
    if source == "ga_global_ree":
        parent = _text(raw.get("District"))
        context = _strings([*context, parent])
    level, level_evidence, flags = _scope(row, raw)
    if is_placeholder_name(primary):
        flags.append("placeholder_primary_name")
    excluded = _strings([v for v in [primary, *identity, *unverified] if is_placeholder_name(v)])
    identity = [v for v in identity if not is_placeholder_name(v)]
    unverified = [v for v in unverified if not is_placeholder_name(v)]
    types_raw = _json(row, "raw_deposit_type", {})
    types, type_evidence = _deposit_types(source, types_raw, vocab)
    links = _source_links(row, raw, reference)
    record_links = [link for link in links if link["kind"] != "download"]
    notes = [
        {"field": field, "raw_value": raw[field]}
        for field in (
            "Rec_Note",
            "Comments",
            "Comments ",
            "Part_of",
            "Components",
            "Includes",
            "comment",
            "facility_type",
            "Asset Type",
            "Contains",
        )
        if _text(raw.get(field))
    ]
    release = None
    if source == GA_SOURCES[0] and row.get("source_url") == GA_WORKBOOK:
        release = GA_RELEASE
    if source == GA_SOURCES[1] and GA_RELEASE in str(raw.get("source", "")):
        release = GA_RELEASE
    return {
        "record_id": row["record_id"],
        "source_id": source,
        "native_id": row.get("native_id"),
        "display_name": _text(primary) or "Name not supplied",
        "primary_name": primary,
        "primary_name_usable": not is_placeholder_name(primary),
        "raw_name": original_name,
        "parent_name": parent,
        "identity_aliases": identity,
        "context_names": context,
        "unverified_aliases": unverified,
        "aliases_raw": aliases,
        "excluded_name_placeholders": excluded,
        "alias_evidence": alias_evidence,
        "entity_level": level,
        "entity_level_evidence": level_evidence,
        "scope_flags": sorted(set(flags)),
        "deposit_types": types,
        "deposit_type_evidence": type_evidence,
        "deposit_types_raw": types_raw,
        "site_url": record_links[0]["url"] if record_links else None,
        "source_url": row.get("source_url"),
        "source_links": links,
        "source_notes": notes,
        "reference": reference,
        "location_precision": row.get("location_precision"),
        "lat": row.get("lat"),
        "lon": row.get("lon"),
        "country": row.get("country"),
        "country_iso3s": _json(row, "country_iso3s", []),
        "pilot_families": _json(row, "pilot_families", []),
        "pilot_commodities": _json(row, "pilot_commodities", []),
        "raw_commodities": _json(row, "raw_commodities", {}),
        "raw_status": _json(row, "raw_status", {}),
        "pilot_evidence": _json(row, "pilot_evidence", []),
        "record_kind": row.get("record_kind"),
        "provenance": {field: row.get(field) for field in PROVENANCE_FIELDS},
        "source_view": {
            "publisher_release": release,
            "currency": row.get("source_currency"),
            "state": raw.get("State") if source == GA_SOURCES[0] else raw.get("state"),
            "status": raw.get("Status") if source == GA_SOURCES[0] else raw.get("status"),
            "commodity_group": raw.get("Commodity Group")
            if source == GA_SOURCES[0]
            else raw.get("commodities"),
        }
        if source in GA_SOURCES
        else None,
    }


def reconcile_source_views(records: list[dict]) -> dict:
    """Reconcile matching GA publisher views, not physical site identities.

    Only unique name/classification signatures with the same documented release, currency,
    state, status, country and commodities qualify. All observations remain
    separate source records; no publisher IDs, crosswalks or merges are invented.
    """
    views = {source: defaultdict(list) for source in GA_SOURCES}
    if len({record["record_id"] for record in records}) != len(records):
        raise ValueError("Duplicate source record ID")
    for record in records:
        if record["source_id"] in views:
            view = record["source_view"]
            signature = tuple(
                str(value or "")
                for value in (
                    normalize_name(record["primary_name"]),
                    view["state"],
                    view["status"],
                    view["commodity_group"],
                )
            )
            views[record["source_id"]][signature].append(record)
    pairs, matched = [], set()
    for signature in sorted(set(views[GA_SOURCES[0]]) & set(views[GA_SOURCES[1]])):
        lefts, rights = (views[source][signature] for source in GA_SOURCES)
        if not signature[0] or len(lefts) != 1 or len(rights) != 1:
            continue
        left, right = lefts[0], rights[0]
        if not left["primary_name_usable"] or not right["primary_name_usable"]:
            continue
        a, b = left["source_view"], right["source_view"]
        if (
            a["publisher_release"] != GA_RELEASE
            or b["publisher_release"] != GA_RELEASE
            or a["currency"] != "2025-12"
            or b["currency"] != "2025-12"
            or any(
                a[key] is None or a[key] != b[key] for key in ("state", "status", "commodity_group")
            )
            or sorted(left["country_iso3s"]) != ["AUS"]
            or sorted(right["country_iso3s"]) != ["AUS"]
            or sorted(left["pilot_commodities"]) != sorted(right["pilot_commodities"])
            or left["record_kind"] != right["record_kind"]
            or any(
                isinstance(record[field], bool)
                or not isinstance(record[field], (int, float))
                or not math.isfinite(record[field])
                or abs(record[field]) > bound
                for record in (left, right)
                for field, bound in (("lat", 90), ("lon", 180))
            )
        ):
            continue
        distance = haversine_km(left["lat"], left["lon"], right["lat"], right["lon"])
        if distance > GA_COORDINATE_TOLERANCE_KM:
            continue
        first, second = sorted((left["record_id"], right["record_id"]))
        pairs.append(
            {
                "left_record_id": first,
                "right_record_id": second,
                "reason": "same_publisher_release_view",
                "evidence": {
                    "publisher_release": GA_RELEASE,
                    "currency": "2025-12",
                    "name": left["primary_name"],
                    "matching_fields": [
                        "unique normalized name/classification signature",
                        "state",
                        "status",
                        "raw commodity group",
                        "pilot commodity codes",
                        "ISO host country",
                        "record kind",
                    ],
                    "distance_km": distance,
                    "coordinate_tolerance_km": GA_COORDINATE_TOLERANCE_KM,
                    "interpretation": (
                        "Two source views; not independent corroboration or a site merge"
                    ),
                },
            }
        )
        matched.update((first, second))
    pairs.sort(key=lambda pair: (pair["left_record_id"], pair["right_record_id"]))
    return {
        "pairs": pairs,
        "groups": [[pair["left_record_id"], pair["right_record_id"]] for pair in pairs],
        "unmatched": sorted(
            record["record_id"]
            for record in records
            if record["source_id"] in GA_SOURCES and record["record_id"] not in matched
        ),
        "policy": (
            "GA 2025 publisher-view correspondence only; no ICMM Contains crosswalk inferred"
        ),
    }
