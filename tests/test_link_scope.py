"""Candidate review scope must not invent mine types or coordinate precision."""

import json
import struct

import pytest

from pipeline.link.scope import UNKNOWN_PRECISION_POLICY, review_eligibility


def row(**changes):
    result = {
        "source_id": "usgs_global_ree",
        "record_kind": "deposit",
        "raw_fields": "{}",
        "raw_deposit_type": "{}",
        "raw_geometry": "{}",
        "lat": 10.0,
        "lon": 20.0,
        "location_precision": None,
    }
    result.update(changes)
    return result


def point_wkb(order="<", x=20.0, y=10.0):
    return struct.pack(order + "BIdd", 1 if order == "<" else 0, 1, x, y)


@pytest.mark.parametrize(
    "precision,radius", [(None, 10), ("exact", 10), ("approximate", 10), ("district", 25)]
)
def test_radii_preserve_reported_or_unknown_precision(precision, radius):
    original = row(location_precision=precision)
    result = review_eligibility(original)
    assert result["eligible"] and result["radius_km"] == radius
    assert original["location_precision"] == precision
    if precision is None:
        assert result["precision_policy"] == UNKNOWN_PRECISION_POLICY
        assert "approximate" not in result["precision_policy"]


@pytest.mark.parametrize(
    "kind", ["source_group", "country_commodity", "finance_asset", "finance", "export_restriction"]
)
def test_context_and_group_records_never_enter_point_review(kind):
    assert review_eligibility(row(record_kind=kind))["reason"] == "record_kind_out_of_scope"


@pytest.mark.parametrize(
    "source,field,value",
    [
        ("icmm", "Asset Type", "Mine"),
        ("jasansky", "facility_type", "Mine"),
        ("ga_critical_2025", "Status", "Mine - care and maintenance"),
        ("ga_critical_2025_arcgis", "status", "Operating mine"),
    ],
)
def test_source_explicit_mines_qualify(source, field, value):
    result = review_eligibility(
        row(source_id=source, record_kind="facility", raw_fields=json.dumps({field: value}))
    )
    assert result["eligible"]


def test_wapha_documented_mine_scope_qualifies_without_invented_type():
    assert review_eligibility(row(source_id="wapha", record_kind="facility"))["eligible"]


@pytest.mark.parametrize(
    "source,field,value,reason",
    [
        ("icmm", "Asset Type", "Mine;Refinery", "mixed_mine_processing_requires_review"),
        ("jasansky", "facility_type", "Mine, Smelter", "mixed_mine_processing_requires_review"),
        ("icmm", "Asset Type", "Plant", "processing_facility"),
        ("jasansky", "facility_type", "Concentrator", "processing_facility"),
        ("jasansky", "facility_type", "Recycling", "processing_facility"),
        ("usgs_mrds", "dev_stat", "Plant", "processing_facility"),
        ("icmm", "Asset Type", "Unknown", "unknown_facility_type_requires_review"),
        ("jasansky", "facility_type", None, "unknown_facility_type_requires_review"),
    ],
)
def test_processing_mixed_and_unknown_types_are_excluded_for_review(source, field, value, reason):
    result = review_eligibility(
        row(source_id=source, record_kind="facility", raw_fields=json.dumps({field: value}))
    )
    assert not result["eligible"] and result["radius_km"] is None
    assert result["reason"] == reason


def test_explicit_marine_type_is_distinguished_from_mineral_habit_and_system():
    explicit = row(
        source_id="usgs_global_critical",
        raw_deposit_type=json.dumps({"DEPOSIT_TYPE": "Sea-floor Fe-Mn(-Ni-Cu-Co-Mo) nodules"}),
    )
    assert review_eligibility(explicit)["reason"] == "marine_nodule_or_deep_sea_type"
    for source, fields in [
        (
            "usgs_us_critical",
            {"minsystem": "Volcanogenic seafloor", "deptype": "Polymetallic sulfide"},
        ),
        (
            "usgs_global_ree",
            {"Dep_Type": "shoreline placer", "Dep_Form": "nodular", "Dep_Note": "marine placer"},
        ),
        ("usgs_global_critical", {"DEPOSIT_TYPE": "Sea-floor massive sulfide"}),
        ("usgs_global_critical", {"DEPOSIT_TYPE": "Marine sedimentary Mn"}),
    ]:
        assert review_eligibility(row(source_id=source, raw_deposit_type=json.dumps(fields)))[
            "eligible"
        ]


def test_explicit_tailings_type_does_not_enter_original_deposit_matching():
    result = review_eligibility(row(raw_deposit_type='{"Dep_Type":"carbonatite; tailings"}'))
    assert result["reason"] == "waste_recovery_or_processing_type"
    assert review_eligibility(row(raw_fields='{"Name":"Old tailings mine"}'))["eligible"]


@pytest.mark.parametrize("order", ["<", ">"])
def test_point_and_singleton_multipoint_wkb_are_safe_point_candidates(order):
    point = point_wkb(order)
    multi = struct.pack(order + "BII", 1 if order == "<" else 0, 4, 1) + point
    for geometry in (point, multi):
        result = review_eligibility(row(raw_geometry=json.dumps(geometry.hex())))
        assert result["eligible"]


@pytest.mark.parametrize(
    "geometry,reason",
    [
        ({"type": "MultiPoint", "coordinates": [[20, 10], [21, 11]]}, "multi_location_geometry"),
        ({"type": "Polygon", "coordinates": [[[20, 10], [21, 11], [20, 10]]]}, "nonpoint_geometry"),
        ({"paths": [[[20, 10], [21, 11]]]}, "nonpoint_geometry"),
        ({"type": "Point", "coordinates": []}, "invalid_geometry"),
        ("01", "invalid_geometry"),
        ("not hex", "invalid_geometry"),
    ],
)
def test_nonpoint_and_invalid_geometry_never_use_supplied_point_coordinates(geometry, reason):
    result = review_eligibility(row(raw_geometry=json.dumps(geometry)))
    assert not result["eligible"] and result["reason"] == reason


def test_singleton_geojson_and_arcgis_points_match_normalized_coordinates():
    for geometry in ({"type": "MultiPoint", "coordinates": [[20, 10]]}, {"x": 20, "y": 10}):
        assert review_eligibility(row(raw_geometry=json.dumps(geometry)))["eligible"]
    result = review_eligibility(row(raw_geometry='{"x":100,"y":10}'))
    assert result["reason"] == "geometry_coordinate_conflict_requires_review"


def test_wkb_unknown_srid_truncated_and_trailing_bytes_are_rejected():
    for data in (point_wkb()[:-1], point_wkb() + b"extra"):
        assert (
            review_eligibility(row(raw_geometry=json.dumps(data.hex())))["reason"]
            == "invalid_geometry"
        )
    ewkb = struct.pack("<BII", 1, 0x20000001, 3857) + struct.pack("<dd", 20, 10)
    assert (
        review_eligibility(row(raw_geometry=json.dumps(ewkb.hex())))["reason"]
        == "unsupported_geometry_crs"
    )


def test_empty_wkb_point_is_unlocated_even_when_numeric_coordinates_are_supplied():
    empty = point_wkb(x=float("nan"), y=float("nan"))
    result = review_eligibility(row(raw_geometry=json.dumps(empty.hex())))
    assert not result["eligible"]
    assert result["reason"] == "empty_geometry"


@pytest.mark.parametrize(
    "changes",
    [
        {"location_precision": "none"},
        {"location_precision": "country centroid"},
        {"lat": None, "lon": None},
        {"lat": 91},
        {"lat": 0, "lon": 0},
    ],
)
def test_unlocated_country_centroids_and_invalid_coordinates_are_excluded(changes):
    assert not review_eligibility(row(**changes))["eligible"]
