"""Phase 0 point-candidate scope for review preparation, never merge permission.

Only geological deposits and explicitly identified mines enter candidate review.
ICMM's Asset Type uses semicolons; Jasansky's facility_type uses commas. Mixed
mine/processing records remain separate review exclusions because the source
point does not distinguish their extraction and processing components. WAPHA's
two workbooks explicitly contain mines; GA's facility statuses identify mines.
"""

from __future__ import annotations

import json
import math
import re
import struct

UNKNOWN_PRECISION_POLICY = (
    "unknown precision; review only; ineligible for future auto-merge without policy/evidence"
)
EXCLUDED_POLICY = "excluded from point candidate generation; original record retained"
PROCESSING_TYPES = {
    "plant",
    "steel plant",
    "refinery",
    "smelter",
    "concentrator",
    "processing",
    "processing plant",
    "recycling",
    "recycling plant",
    "waste recovery",
    "waste-recovery",
}
GA_MINE_STATUSES = {
    "Operating mine",
    "Mine - care and maintenance",
    "Mine - under development",
}
DEPOSIT_FIELDS = {
    "usgs_global_ree": {"Dep_Type"},
    "usgs_global_critical": {"DEPOSIT_TYPE"},
    "usgs_us_critical": {"deptype"},
    "usgs_laterites": {"Subtype"},
    "gseu_crm": {"Deposit type"},
    "ga_global_ree": {"Deposit type"},
}
WASTE_OR_PROCESSING = re.compile(
    r"\b(?:tailings?|mine waste|waste[- ]recovery|recycling|refiner(?:y|ies)|smelter|"
    r"smelting|processing plant)\b",
    re.I,
)
DEEP_SEA = re.compile(r"\bdeep[- ](?:sea|ocean)\b", re.I)
MARINE_NODULE = re.compile(
    r"\b(?:marine|oceanic|ocean|sea[- ]floor|seabed|polymetallic)\b.*\b(?:nodules?|crusts?)\b|"
    r"\b(?:nodules?|crusts?)\b.*\b(?:marine|oceanic|ocean|sea[- ]floor|seabed)\b",
    re.I,
)


def _object(row: dict, field: str) -> dict:
    value = row.get(field, {})
    if isinstance(value, str):
        value = json.loads(value)
    if not isinstance(value, dict):
        raise ValueError(f"Expected a JSON object in {field}")
    return value


def _result(eligible: bool, reason: str, radius=None, policy=EXCLUDED_POLICY) -> dict:
    return {
        "eligible": eligible,
        "reason": reason,
        "radius_km": radius,
        "precision_policy": policy,
    }


def _facility_scope(source: str, raw: dict) -> str | None:
    """Return an exclusion reason, or None for an explicitly identified mine."""
    if source == "usgs_mrds":
        return "processing_facility"
    if source == "wapha":
        return None  # Source workbooks are world_active/inactive_mines, not all facilities.
    if source in {"ga_critical_2025", "ga_critical_2025_arcgis"}:
        status = raw.get("Status" if source == "ga_critical_2025" else "status")
        return None if status in GA_MINE_STATUSES else "unknown_facility_type_requires_review"
    if source == "icmm":
        value, delimiter = raw.get("Asset Type"), ";"
    elif source == "jasansky":
        value, delimiter = raw.get("facility_type"), ","
    else:
        return "unknown_facility_type_requires_review"
    if not isinstance(value, str) or not value.strip():
        return "unknown_facility_type_requires_review"
    types = {part.strip().casefold() for part in value.split(delimiter) if part.strip()}
    if types == {"mine"}:
        return None
    if types & PROCESSING_TYPES:
        return "mixed_mine_processing_requires_review" if "mine" in types else "processing_facility"
    return "unknown_facility_type_requires_review"


def _deposit_scope(source: str, deposits: dict) -> str | None:
    for field, value in deposits.items():
        supported = field in DEPOSIT_FIELDS.get(source, set()) or (
            source == "ga_global_ree" and field.startswith("Deposit type [")
        )
        if not supported or not isinstance(value, str):
            continue
        if WASTE_OR_PROCESSING.search(value):
            return "waste_recovery_or_processing_type"
        # Source deposit type is required. Names, mineral systems, host rocks,
        # mineral habits ("nodular") and generic marine placers are not this scope.
        if DEEP_SEA.search(value) or MARINE_NODULE.search(value):
            return "marine_nodule_or_deep_sea_type"
    return None


def _xy(value) -> tuple[float, float] | None:
    if not isinstance(value, (list, tuple)) or len(value) < 2:
        return None
    try:
        x, y = (float(v) if not isinstance(v, bool) else math.nan for v in value[:2])
    except (TypeError, ValueError):
        return None
    if not (math.isfinite(x) and math.isfinite(y) and -180 <= x <= 180 and -90 <= y <= 90):
        return None
    return x, y


def _wkb_point(data: bytes, offset=0, *, nested=False):
    """Inspect bounded WKB Point/MultiPoint headers; never compute a centroid."""
    if len(data) - offset < 5 or data[offset] not in (0, 1):
        return "invalid_geometry", None, offset
    order = "<" if data[offset] else ">"
    raw_type = struct.unpack_from(order + "I", data, offset + 1)[0]
    offset += 5
    base = raw_type & 0x1FFFFFFF
    z = bool(raw_type & 0x80000000)
    m = bool(raw_type & 0x40000000)
    if 1000 <= base < 4000:
        dimension, base = divmod(base, 1000)
        z, m = z or dimension in (1, 3), m or dimension in (2, 3)
    if raw_type & 0x20000000:
        if len(data) - offset < 4:
            return "invalid_geometry", None, offset
        srid = struct.unpack_from(order + "I", data, offset)[0]
        offset += 4
        if srid != 4326:
            return "unsupported_geometry_crs", None, offset
    if base == 1:
        width = 8 * (2 + z + m)
        if len(data) - offset < width:
            return "invalid_geometry", None, offset
        coords = struct.unpack_from(order + "dd", data, offset)
        if all(math.isnan(value) for value in coords):
            return "empty_geometry", None, offset + width
        point = _xy(coords)
        return ("point" if point else "invalid_geometry"), point, offset + width
    if base == 4 and not nested:
        if len(data) - offset < 4:
            return "invalid_geometry", None, offset
        count = struct.unpack_from(order + "I", data, offset)[0]
        offset += 4
        if count == 0:
            return "empty_geometry", None, offset
        if count > 1:
            return "multi_location_geometry", None, offset
        return _wkb_point(data, offset, nested=True)
    if base in {2, 3, 5, 6, 7}:
        return "nonpoint_geometry", None, offset
    return "invalid_geometry", None, offset


def _geometry(row: dict):
    geometry = row.get("raw_geometry")
    if isinstance(geometry, str):
        try:
            geometry = json.loads(geometry)
        except json.JSONDecodeError:
            # Already-decoded WKB hex is also supported; staged strings are JSON.
            pass
    if geometry is None or geometry == {}:
        return "absent", None
    if isinstance(geometry, str):
        # A malformed source value must not cause an unbounded binary allocation.
        if len(geometry) > 16 * 1024 * 1024:
            return "invalid_geometry", None
        try:
            data = bytes.fromhex(geometry)
        except ValueError:
            return "invalid_geometry", None
        status, point, end = _wkb_point(data)
        if status == "point" and end != len(data):
            return "invalid_geometry", None
        return status, point
    if not isinstance(geometry, dict):
        return "invalid_geometry", None
    kind = geometry.get("type")
    if kind == "Point":
        point = _xy(geometry.get("coordinates"))
    elif kind == "MultiPoint" or "points" in geometry:
        coords = geometry.get("coordinates") if kind else geometry.get("points")
        if not isinstance(coords, list):
            return "invalid_geometry", None
        if len(coords) != 1:
            return ("multi_location_geometry" if coords else "empty_geometry"), None
        point = _xy(coords[0])
    elif kind in {"LineString", "MultiLineString", "Polygon", "MultiPolygon", "GeometryCollection"}:
        return "nonpoint_geometry", None
    elif kind is None and "x" in geometry and "y" in geometry:
        spatial = geometry.get("spatialReference")
        if spatial and spatial.get("latestWkid", spatial.get("wkid")) != 4326:
            return "unsupported_geometry_crs", None
        point = _xy([geometry["x"], geometry["y"]])
    elif "paths" in geometry or "rings" in geometry:
        return "nonpoint_geometry", None
    else:
        return "invalid_geometry", None
    return ("point" if point else "invalid_geometry"), point


def review_eligibility(row: dict) -> dict:
    """Select point candidates for human-review preparation, without changing rows."""
    source, kind = row.get("source_id"), row.get("record_kind")
    if kind not in {"deposit", "facility"}:
        return _result(False, "record_kind_out_of_scope")
    raw = _object(row, "raw_fields")
    if source == "usgs_mrds" and raw.get("dev_stat") == "Plant":
        return _result(False, "processing_facility")
    if kind == "facility":
        exclusion = _facility_scope(source, raw)
    else:
        exclusion = _deposit_scope(source, _object(row, "raw_deposit_type"))
    if exclusion:
        return _result(False, exclusion)
    geometry_status, geometry_point = _geometry(row)
    if geometry_status not in {"point", "absent"}:
        return _result(False, geometry_status)
    point = _xy([row.get("lon"), row.get("lat")])
    if point is None or point == (0.0, 0.0):
        return _result(False, "unlocated_or_invalid_coordinates")
    if geometry_point is not None and any(
        abs(a - b) > 1e-6 for a, b in zip(point, geometry_point, strict=True)
    ):
        return _result(False, "geometry_coordinate_conflict_requires_review")
    precision = row.get("location_precision")
    if precision is None:
        return _result(True, "eligible_point_for_review", 10.0, UNKNOWN_PRECISION_POLICY)
    if precision in {"exact", "approximate", "district"}:
        return _result(
            True,
            "eligible_point_for_review",
            25.0 if precision == "district" else 10.0,
            f"source-reported {precision} precision; candidate review only",
        )
    if precision in {"none", "country centroid"}:
        return _result(False, "location_precision_excluded")
    return _result(False, "unknown_precision_label_requires_review")
