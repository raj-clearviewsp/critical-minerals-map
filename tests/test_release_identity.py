"""Regression checks for approved evidence, source scope, quarantine and stable IDs."""

import csv
import io

import pytest

from pipeline.release_identity.core import queue_csv
from pipeline.release_identity.identity import project
from pipeline.release_identity.registry import allocate, parquet_bytes, preserves_ids, read_registry


def record(name, level="site", **overrides):
    return {
        "record_id": name,
        "source_id": "first",
        "display_name": name,
        "entity_level": level,
        "primary_name_usable": True,
        "scope_flags": [],
        "location_precision": None,
        "entity_level_evidence": [{"field": "kind", "raw_value": "Mine"}],
        "lat": 10.0,
        "lon": 20.0,
        "country_iso3s": ["AUS"],
        "provenance": {"evidence_grade": "B", "retrieved_at": "2026-10-31"},
        **overrides,
    }


def decision(a, b, label):
    return {
        "pair_id": f"{a}-{b}",
        "left_record_id": a,
        "right_record_id": b,
        "label": label,
        "latest_event_id": f"event:{a}-{b}",
    }


def release(rows, decisions=(), views=(), scope=None):
    records = {r["record_id"]: r for r in rows}
    preview = {
        k: {
            "record_id": k,
            "name": k,
            "lat": r["lat"],
            "lon": r["lon"],
            "minerals": ["Ni"],
            "source_url": "https://example.gov/deposits",
        }
        for k, r in records.items()
    }
    return project(records, preview, decisions, views, [], "2026-11-04", scope)[0]


def test_only_direct_approved_pairs_group_source_twins_stay_provisional():
    r = release(
        [
            record("a"),
            record("mirror"),
            record("different", lat=12.0),
            record("regional", "district_area"),
        ],
        [decision("a", "different", "same_site")],
        [["a", "mirror"]],
    )
    assert len(r["observations"]) == 4
    by_record = {o["record_id"]: o for o in r["observations"]}
    assert (
        by_record["a"]["site_id"]
        == by_record["different"]["site_id"]
        != by_record["mirror"]["site_id"]
    )
    assert by_record["a"]["publisher_view_counterparts"] == ["mirror"]
    assert by_record["regional"]["site_id"] is None
    joined = next(s for s in r["sites"] if s["review_state"] == "reviewed_physical_site")
    assert (
        joined["lat"] == 10
        and joined["coordinate_record_id"] == "a"
        and joined["location_precision"] is None
    )


@pytest.mark.parametrize(
    "label", [None, "different_sites", "left_contains_right", "related_scope_unclear"]
)
def test_incomplete_or_contradictory_transitive_chain_is_quarantined(label):
    d = [decision("a", "b", "same_site"), decision("b", "c", "same_site")]
    if label:
        d.append(decision("a", "c", label))
    r = release([record(n) for n in "abc"], d)
    assert not r["sites"] and r["identity-groups"][0]["disposition"] == "quarantine"
    assert (
        len(r["identity-groups"][0]["unchecked_internal_pairs" if label is None else "conflicts"])
        == 1
    )


def test_indirect_view_conflict_and_containment_cycle_are_quarantined():
    d = [
        decision("a", "b", "same_site"),
        decision("mirror", "b", "different_sites"),
        decision("x", "y", "left_contains_right"),
        decision("y", "z", "left_contains_right"),
        decision("z", "x", "left_contains_right"),
    ]
    r = release([record(n) for n in ["a", "b", "mirror", "x", "y", "z"]], d, [["a", "mirror"]])
    assert not r["sites"]
    assert sorted(i["reason"] for i in r["constraint-conflicts"]) == [
        "conflicts_with_identity_or_publisher_view",
        *["containment_cycle"] * 3,
    ]


def test_physical_scope_requires_explicit_crosswalk_and_never_lifts_centroids():
    d = [decision("a", "b", "same_site")]
    rows = [record("a", "unknown"), record("b", "district_area", lon=21.0)]
    assert not release(rows, d)["sites"]
    scope = {
        "a-b": {"anchor_record_id": "b", "basis": "Approved independent deposit-location crosswalk"}
    }
    s = release(rows, d, scope=scope)["sites"][0]
    assert s["lon"] == 21 and s["coordinate_record_id"] == "b"
    assert not release(
        [rows[0], {**rows[1], "location_precision": "country_centroid"}], d, scope=scope
    )["sites"]


def test_registry_reordering_extension_older_id_and_split_protection():
    first, rows = allocate([["z"], ["a", "b"]], [], "2026-11-04")
    assert first["a"] == first["b"]
    second, repeated = allocate([["b", "a"], ["z"]], rows, "2026-11-05")
    assert second == first and repeated == rows
    third, grown = allocate([["new", "z"], ["a", "b"], ["other"]], rows, "2026-11-05")
    assert third["new"] == first["z"] and third["other"] != first["z"]
    merged, combined = allocate([["a", "b", "z", "new", "other"]], grown, "2026-11-06")
    assert set(merged.values()) == {min(first.values())}
    assert (
        preserves_ids(parquet_bytes(rows), parquet_bytes(combined))
        and read_registry(parquet_bytes(combined)) == combined
    )
    with pytest.raises(ValueError, match="split"):
        allocate([["a"], ["b"]], rows, "2026-11-06")
    altered = [
        {**r, "current_site_id": third["other"]} if r["record_id"] == "a" else r for r in combined
    ]
    assert not preserves_ids(parquet_bytes(rows), parquet_bytes(altered))


def test_queue_keeps_abstention_orientation_and_unreviewed_view():
    d = [decision("a", "b", "insufficient_evidence"), decision("a", "c", "different_sites")]
    pairs = [
        {
            **decision(a, b, ""),
            "left_name": a,
            "right_name": b,
            "distance_km": 1.0,
            "queue": "ambiguous",
        }
        for a, b in [("a", "b"), ("a", "c"), ("b", "c"), ("b", "d")]
    ]
    raw, counts, neighbors = queue_csv(pairs, d, [], [["b", "d"]])
    rows = list(csv.DictReader(io.StringIO(raw.decode())))
    assert counts == {
        "unreviewed": 1,
        "unreviewed_publisher_view": 1,
        "resolved_direct_decision": 1,
        "reviewed_unresolved": 1,
    }
    assert (
        rows[0]["label"] == "insufficient_evidence"
        and rows[0]["review_event_id"] == "event:a-b"
        and neighbors["b"] == 2
    )


def test_amendment_rejects_unapproved_auto_policy(tmp_path):
    import yaml

    from pipeline.release_identity.core import APPROVAL_RECEIPT, POLICY, approved_policy

    policy = {
        "release": "2026.11.04-v0",
        "approved_on": "2026-11-04",
        "approved_by": "Raj Sawhney",
        "approval": "approved",
        "scope": ["reviewed private release"],
        "basis": APPROVAL_RECEIPT,
        "identity_method": "explicitly_reviewed",
        "automatic_merging": False,
        "future_automatic_precision_minimum": 0.97,
    }
    p = tmp_path / POLICY
    p.parent.mkdir()
    b = tmp_path / APPROVAL_RECEIPT
    b.parent.mkdir()
    b.write_text("{}")
    p.write_text(yaml.safe_dump(policy))
    assert approved_policy(tmp_path)["future_automatic_precision_minimum"] == 0.97
    for mutation in [
        {"automatic_merging": True},
        {"future_automatic_precision_minimum": 0.96},
        {"basis": "unrelated"},
    ]:
        p.write_text(yaml.safe_dump({**policy, **mutation}))
        with pytest.raises(ValueError, match="amendment"):
            approved_policy(tmp_path)
