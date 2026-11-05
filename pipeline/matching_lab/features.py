"""Source-only features; labels, research and old routing never enter the model."""

from __future__ import annotations

import math
import re

from pipeline.link.candidates import haversine_km, normalize_name

LEVELS = {"name": 6, "distance": 6, "scope": 4, "geology": 3}
GENERIC = {
    "mine",
    "mines",
    "deposit",
    "project",
    "prospect",
    "occurrence",
    "group",
    "area",
    "district",
    "complex",
    "operation",
    "operations",
    "nickel",
    "cobalt",
    "copper",
    "gold",
    "silver",
    "rare",
    "earth",
    "ree",
    "unknown",
    "unnamed",
    "none",
    "na",
}
DIRECTIONS = {
    "north",
    "south",
    "east",
    "west",
    "northern",
    "southern",
    "eastern",
    "western",
    "northeast",
    "northwest",
    "southeast",
    "southwest",
}
BROAD = {"operation_complex", "district_area"}
KNOWN = {"site", "subsite", *BROAD}


def names(record: dict) -> tuple[str, ...]:
    values = list(record.get("identity_aliases", []))
    if record.get("primary_name_usable", True):
        values.append(record.get("primary_name") or record.get("display_name"))
    result = set()
    for value in values:
        if value is None:
            continue
        if not isinstance(value, str):
            raise ValueError("Identity names must be strings")
        normalized = normalize_name(value)
        if (
            normalized
            and not re.match(r"^(unknown|unnamed|unidentified)\b", normalized)
            and set(normalized.split()) - GENERIC
        ):
            result.add(normalized)
    return tuple(sorted(result))


def coordinates(record: dict) -> tuple[float, float] | None:
    for key, limit in (("lat", 90), ("lon", 180)):
        value = record.get(key)
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
            or not -limit <= value <= limit
        ):
            return None
    if record["lat"] == record["lon"] == 0:
        return None
    return float(record["lat"]), float(record["lon"])


def compare(left: dict, right: dict) -> dict:
    from rapidfuzz.fuzz import ratio

    if not left.get("record_id") or not right.get("record_id"):
        raise ValueError("Source record identities are required")
    if left["record_id"] == right["record_id"]:
        raise ValueError("Cannot compare a record to itself")
    a, b = names(left), names(right)
    score = max((ratio(x, y) for x in a for y in b), default=0.0)
    name = (
        (
            0
            if set(a) & set(b)
            else 1
            if score >= 95
            else 2
            if score >= 85
            else 3
            if score >= 65
            else 4
        )
        if a and b
        else 5
    )
    x, y = coordinates(left), coordinates(right)
    distance = haversine_km(*x, *y) if x and y else None
    band = next(
        (
            i
            for i, cutoff in enumerate([0.25, 2, 5, 10, 25])
            if distance is not None and distance <= cutoff
        ),
        5,
    )
    la, lb = left.get("entity_level", "unknown"), right.get("entity_level", "unknown")
    scope = (
        3
        if la not in KNOWN or lb not in KNOWN
        else 2
        if (la in BROAD) != (lb in BROAD)
        else 1
        if la in BROAD or lb in BROAD
        else 0
    )
    ta = set(left.get("deposit_types", [])) - {"unknown"}
    tb = set(right.get("deposit_types", [])) - {"unknown"}
    geology = 0 if ta & tb else 1 if ta and tb else 2
    guards = []
    if not a or not b:
        guards.append("missing_or_generic_identity_name")
    if distance is None:
        guards.append("invalid_or_missing_coordinates")
    elif distance > 25:
        guards.append("outside_supported_distance")
    if scope == 3:
        guards.append("unresolved_entity_scope")
    elif scope == 2 or (scope == 1 and la != lb):
        guards.append("incompatible_entity_scope")
    flags = set(left.get("scope_flags", [])) | set(right.get("scope_flags", []))
    for flag in ("publisher_cluster_record", "qualified_entity_level"):
        if flag in flags:
            guards.append(flag)
    ca, cb = set(left.get("country_iso3s", [])), set(right.get("country_iso3s", []))
    if ca and cb and not ca & cb:
        guards.append("country_conflict")
    fa, fb = set(left.get("pilot_families", [])), set(right.get("pilot_families", []))
    if not fa or not fb:
        guards.append("missing_commodity_context")
    elif not fa & fb:
        guards.append("commodity_conflict")
    primary = [
        normalize_name(r.get("primary_name") or r.get("display_name")) for r in (left, right)
    ]
    numbers = [set(re.findall(r"\d+", n)) for n in primary]
    directions = [set(n.split()) & DIRECTIONS for n in primary]
    # Trusted exact identity aliases can explain spelling/qualifier differences.
    if name != 0:
        if numbers[0] != numbers[1] and (numbers[0] or numbers[1]):
            guards.append("numbered_feature_difference")
        if directions[0] != directions[1] and (directions[0] or directions[1]):
            guards.append("directional_feature_difference")
    processing = [bool(re.search(r"\b(plant|refinery|smelter|concentrator)\b", n)) for n in primary]
    if processing[0] != processing[1]:
        guards.append("extraction_processing_scope")
    for child, parent, other_names in ((left, right, b), (right, left, a)):
        stated = normalize_name(child.get("parent_name"))
        if stated and stated in other_names and names(child) != names(parent):
            guards.append("explicit_parent_component")
    return {
        "levels": {"name": name, "distance": band, "scope": scope, "geology": geology},
        "distance_km": distance,
        "name_similarity": score,
        "guards": sorted(set(guards)),
    }
