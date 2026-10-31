"""Verified source-view duplicates reduce work without collapsing physical records."""

import copy

import pytest

from pipeline.triage.units import ReviewUnitError, build_review_units


def row(pair_id, left, right, queue="likely_match", **extra):
    return {
        "pair_id": pair_id,
        "left_record_id": left,
        "right_record_id": right,
        "queue": queue,
        "reason_codes": ["test_evidence"],
        **extra,
    }


def views(*groups):
    return {
        "groups": [list(group) for group in groups],
        "pairs": [
            {
                "left_record_id": group[0],
                "right_record_id": right,
                "reason": "same_publisher_release_view",
                "evidence": {"release": "verified"},
            }
            for group in groups
            for right in group[1:]
        ],
    }


def test_verified_views_create_one_external_comparison_and_keep_internal_audit():
    rows = [row("pair_internal", "A1", "A2"), row("pair_b", "A1", "B"), row("pair_a", "A2", "B")]
    source_views = views(("A1", "A2"))
    before = copy.deepcopy((rows, source_views))
    result = build_review_units(rows, source_views)
    assert len(result["units"]) == 1
    unit = result["units"][0]
    assert unit["canonical_record_ids"] == ["A1", "B"]
    assert unit["member_pair_ids"] == ["pair_a", "pair_b"]
    assert unit["representative_pair_id"] == "pair_a"
    assert unit["member_pair_count"] == 2 and not unit["auto_merge_eligible"]
    assert result["pair_to_unit"]["pair_a"] == result["pair_to_unit"]["pair_b"] == unit["unit_id"]
    assert result["pair_to_unit"]["pair_internal"] is None
    assert result["internal_pair_ids"] == ["pair_internal"]
    summary = result["summary"]
    assert summary["input_candidate_count"] == 3
    assert summary["source_view_internal_pairs"] == 1
    assert summary["review_units"] == summary["duplicate_comparisons_collapsed"] == 1
    assert summary["unit_queue_counts"] == {"likely_match": 1}
    assert (rows, source_views) == before


def test_workload_is_independent_of_row_group_and_pair_order():
    rows = [
        row("p1", "A2", "B2"),
        row("p2", "A1", "B1"),
        row("p3", "B1", "A2"),
        row("i1", "A1", "A2"),
        row("i2", "B1", "B2"),
    ]
    source_views = views(("A1", "A2"), ("B1", "B2"))
    expected = build_review_units(rows, source_views)
    altered = copy.deepcopy(source_views)
    altered["groups"] = [list(reversed(g)) for g in reversed(altered["groups"])]
    altered["pairs"].reverse()
    for pair in altered["pairs"]:
        pair["left_record_id"], pair["right_record_id"] = (
            pair["right_record_id"],
            pair["left_record_id"],
        )
    assert build_review_units(list(reversed(rows)), altered) == expected
    assert expected["summary"]["review_units"] == 1


def test_queue_disagreement_becomes_an_explicit_ambiguous_unit():
    rows = [
        row("a", "A1", "B", "likely_match", reason_codes=["strong_name"]),
        row("b", "A2", "B", "likely_distinct", reason_codes=["distance"]),
    ]
    result = build_review_units(rows, views(("A1", "A2")))
    unit = result["units"][0]
    assert unit["queue"] == "ambiguous" and unit["requires_adjudication"]
    assert unit["queue_conflict"]
    assert unit["member_queue_counts"] == {"likely_distinct": 1, "likely_match": 1}
    assert unit["reason_codes"] == [
        "distance",
        "publisher_view_routing_disagreement",
        "strong_name",
    ]


def test_identical_coordinates_or_names_never_create_view_groups():
    rows = [
        row("a", "A", "B", lat=1, lon=2, left_name="Mine", right_name="Mine"),
        row("b", "C", "B", lat=1, lon=2, left_name="Mine", right_name="Mine"),
    ]
    result = build_review_units(rows, views())
    assert len(result["units"]) == 2
    assert result["summary"]["duplicate_comparisons_collapsed"] == 0
    assert result["summary"]["source_view_internal_pairs"] == 0


@pytest.mark.parametrize(
    "change",
    [
        lambda v: v["groups"].append(["A2", "B"]),
        lambda v: v["groups"].append(["C", "C"]),
        lambda v: v["groups"].append(["single"]),
        lambda v: v["pairs"].clear(),
        lambda v: v["pairs"].append(copy.deepcopy(v["pairs"][0])),
        lambda v: v["pairs"][0].update(right_record_id="unknown"),
        lambda v: v["pairs"][0].update(reason="similar_name"),
        lambda v: v["pairs"][0].update(evidence=None),
    ],
)
def test_inconsistent_or_unverified_view_groups_are_rejected(change):
    source_views = views(("A1", "A2"))
    change(source_views)
    with pytest.raises(ReviewUnitError):
        build_review_units([row("a", "A1", "B"), row("b", "A2", "B")], source_views)


def test_disconnected_view_group_cannot_silently_merge_comparisons():
    source_views = views(("A1", "A2", "A3", "A4"))
    source_views["pairs"] = [
        {
            "left_record_id": left,
            "right_record_id": right,
            "reason": "same_publisher_release_view",
            "evidence": {},
        }
        for left, right in (("A1", "A2"), ("A3", "A4"))
    ]
    with pytest.raises(ReviewUnitError, match="connected"):
        build_review_units([row("x", "A1", "B")], source_views)


@pytest.mark.parametrize(
    "rows",
    [
        [row("a", "A", "B"), row("a", "C", "D")],
        [row("a", "A", "B"), row("b", "B", "A")],
        [row("a", "A", "A")],
        [row("a", "A", "B", "invented")],
        [row("a", "A", "B", reason_codes="bad")],
    ],
)
def test_malformed_candidate_rows_fail(rows):
    with pytest.raises(ReviewUnitError):
        build_review_units(rows, views())


def test_groups_for_unavailable_candidate_records_are_rejected():
    with pytest.raises(ReviewUnitError, match="absent"):
        build_review_units([row("a", "A1", "B")], views(("A1", "A2")))


def test_empty_universe_reconciles_without_sources():
    result = build_review_units([], views())
    assert result["units"] == [] and result["pair_to_unit"] == {}
    assert result["summary"]["input_candidate_count"] == result["summary"]["review_units"] == 0
