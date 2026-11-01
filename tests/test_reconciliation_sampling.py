"""Benchmark splits isolate candidate neighborhoods and retain review history."""

import copy
import random
from collections import Counter

import pytest

from pipeline.reconciliation import sampling
from pipeline.triage.units import build_review_units


def pair(identity, queue="ambiguous", *, left=None, right=None, distance=1.0):
    return {
        "pair_id": identity,
        "left_record_id": left or f"a:{identity}",
        "right_record_id": right or f"b:{identity}",
        "queue": queue,
        "reason_codes": ["fixture_evidence"],
        "source_pair": ["source_a", "source_b"],
        "shared_families": ["Ni"],
        "distance_km": distance,
    }


def views(*groups):
    return {
        "groups": [list(group) for group in groups],
        "pairs": [
            {
                "left_record_id": group[0],
                "right_record_id": record,
                "reason": "same_publisher_release_view",
                "evidence": {"verified": True},
            }
            for group in groups
            for record in group[1:]
        ],
    }


@pytest.fixture
def candidates():
    rows = [pair(f"likely_match_{i}", "likely_match", distance=0.1) for i in range(3)]
    for queue in ("likely_distinct", "relationship", "ambiguous"):
        for number in range(100):
            row = pair(f"{queue}_{number}", queue, distance=(0.1, 1.0, 6.0)[number % 3])
            row["source_pair"] = ["source_a", f"source_{number % 4}"]
            row["shared_families"] = [["Ni"], ["Co"], ["REE"], ["Co", "Ni"]][number % 4]
            rows.append(row)
    return rows


def select(rows, prior=(), source_views=None):
    source_views = source_views or views()
    units = build_review_units(rows, source_views)
    return sampling.select_benchmark(rows, units, source_views, set(prior))


def test_exact_quota_and_no_invented_labels(candidates):
    selected, summary = select(candidates)
    assert len(selected) == 200
    assert len({row["review_unit_id"] for row in selected}) == 200
    assert len({row["pair_id"] for row in selected}) == 200
    assert Counter(row["split"] for row in selected) == {"development": 120, "holdout": 80}
    for split, quotas in (
        ("development", sampling.DEVELOPMENT_QUOTAS),
        ("holdout", sampling.HOLDOUT_QUOTAS),
    ):
        actual = Counter(row["queue"] for row in selected if row["split"] == split)
        assert all(actual[key] == value for key, value in quotas.items())
    assert summary["human_labels_created"] == 0
    assert summary["model_fitted"] is False
    assert all("label" not in row for row in selected)


def test_shuffled_inputs_are_deterministic_and_not_mutated(candidates):
    source_views = views()
    units = build_review_units(candidates, source_views)
    prior = {"ambiguous_0", "relationship_0", "likely_distinct_0"}
    original = copy.deepcopy((candidates, units, source_views, prior))
    expected = sampling.select_benchmark(candidates, units, source_views, prior)
    assert (candidates, units, source_views, prior) == original
    shuffled_rows, shuffled_units = copy.deepcopy((candidates, units))
    rng = random.Random(91)
    rng.shuffle(shuffled_rows)
    rng.shuffle(shuffled_units["units"])
    actual = sampling.select_benchmark(shuffled_rows, shuffled_units, source_views, prior)
    assert actual == expected


def test_candidate_chains_isolate_full_neighborhoods(candidates):
    # An unselected bridge still binds two otherwise independent review pairs.
    first = candidates[7]
    second = candidates[150]
    bridge = pair(
        "unselected_bridge",
        left=first["right_record_id"],
        right=second["left_record_id"],
    )
    rows = candidates + [bridge]
    selected, _ = select(rows, [first["pair_id"]])
    components = sampling.candidate_components(rows, views())
    component = components[first["left_record_id"]]
    assert component == components[second["right_record_id"]]
    assert component == components[bridge["left_record_id"]]
    assert all(
        row["split"] == "development" for row in selected if row["split_component"] == component
    )
    dev = {row["split_component"] for row in selected if row["split"] == "development"}
    holdout = {row["split_component"] for row in selected if row["split"] == "holdout"}
    assert not dev & holdout


def test_history_is_retained_only_in_development(candidates):
    prior = {"likely_match_0", "likely_distinct_0", "relationship_0", "ambiguous_0"}
    selected, summary = select(candidates, prior)
    retained = [row for row in selected if row["historical_pair_ids"]]
    assert {identity for row in retained for identity in row["historical_pair_ids"]} == prior
    assert {row["split"] for row in retained} == {"development"}
    assert summary["retained_historical_units"] == 4
    assert summary["new_units"] == 196


def test_equivalent_views_connect_neighborhoods_and_exclude_internal_pair(candidates):
    base = pair("view_a_other", left="publisher:a", right="other:x")
    equivalent = pair("view_b_other", left="publisher:b", right="other:x")
    internal = pair("view_internal", "likely_match", left="publisher:a", right="publisher:b")
    distant_chain = pair("view_b_neighbor", left="publisher:b", right="other:y")
    source_views = views(("publisher:a", "publisher:b"))
    rows = candidates + [base, equivalent, internal, distant_chain]
    selected, summary = select(rows, [equivalent["pair_id"], internal["pair_id"]], source_views)
    components = sampling.candidate_components(rows, source_views)
    assert len({components[x] for x in ("publisher:a", "publisher:b", "other:x", "other:y")}) == 1
    retained = next(row for row in selected if equivalent["pair_id"] in row["historical_pair_ids"])
    assert retained["pair_id"] == equivalent["pair_id"]
    assert set(retained["equivalent_pair_ids"]) == {base["pair_id"], equivalent["pair_id"]}
    assert retained["split"] == "development"
    assert all(internal["pair_id"] not in row["equivalent_pair_ids"] for row in selected)
    assert summary["retained_historical_units"] == 1
    assert summary["historical_source_view_pairs_excluded"] == 1


def test_source_view_equivalence_binds_disconnected_candidate_edges():
    rows = [pair("one", left="a", right="x"), pair("two", left="b", right="y")]
    result = sampling.candidate_components(rows, views(("a", "b")))
    assert result == {"a": "a", "b": "a", "x": "a", "y": "a"}
    shuffled = list(reversed(rows))
    assert sampling.candidate_components(shuffled, views(("b", "a"))) == result


def test_insufficient_candidates_fail_explicitly(candidates):
    reduced = [row for row in candidates if row["queue"] != "likely_distinct"]
    with pytest.raises(ValueError, match="Not enough eligible cases"):
        select(reduced)


def test_prior_component_cannot_supply_holdout_even_if_pair_is_new(candidates):
    rows = copy.deepcopy(candidates)
    # All ambiguous pairs now belong to one candidate neighborhood already exposed.
    for row in rows:
        if row["queue"] == "ambiguous":
            row["left_record_id"] = "exposed:shared-record"
    with pytest.raises(ValueError, match="Not enough eligible cases for holdout:ambiguous"):
        select(rows, ["ambiguous_0"])


def test_historical_quota_overflow_cannot_silently_drop_reviews(candidates):
    with pytest.raises(ValueError, match="Historical cases exceed a development quota"):
        select(candidates, {f"relationship_{i}" for i in range(36)})


@pytest.mark.parametrize("problem", ["duplicate_pair_id", "unknown_history"])
def test_duplicate_or_unknown_candidate_ids_rejected(candidates, problem):
    units = build_review_units(candidates, views())
    rows = (
        candidates + [copy.deepcopy(candidates[0])]
        if problem == "duplicate_pair_id"
        else candidates
    )
    prior = {"not_a_candidate"} if problem == "unknown_history" else set()
    with pytest.raises(ValueError, match="Duplicate candidates or unknown historical pair"):
        sampling.select_benchmark(rows, units, views(), prior)


@pytest.mark.parametrize(
    "distance,band", [(0, "0–0.25 km"), (0.25, "0–0.25 km"), (2, "0.25–2 km"), (2.01, "2–10 km")]
)
def test_strata_keep_exact_distance_boundaries_and_sorted_families(distance, band):
    row = pair("boundary", distance=distance)
    row["shared_families"] = ["Ni", "Co"]
    assert sampling.stratum(row).endswith(f"Co,Ni | {band}")


def test_validator_rejects_component_leakage_and_exposed_holdout(candidates):
    selected, _ = select(candidates)
    held = next(row for row in selected if row["split"] == "holdout")
    with pytest.raises(ValueError, match="Previously reviewed"):
        sampling.validate_selection(selected, {held["pair_id"]})
    leaked = copy.deepcopy(selected)
    dev = next(row for row in leaked if row["split"] == "development")
    held = next(row for row in leaked if row["split"] == "holdout")
    held["split_component"] = dev["split_component"]
    with pytest.raises(ValueError, match="neighborhoods leak"):
        sampling.validate_selection(leaked, set())


def test_validator_rejects_changed_quota_or_repeated_unit(candidates):
    selected, _ = select(candidates)
    changed = copy.deepcopy(selected)
    changed[0]["queue"] = "unrecognized"
    with pytest.raises(ValueError, match="quota mismatch"):
        sampling.validate_selection(changed, set())
    repeated = copy.deepcopy(selected)
    repeated[0]["review_unit_id"] = repeated[1]["review_unit_id"]
    with pytest.raises(ValueError, match="200 distinct review units"):
        sampling.validate_selection(repeated, set())
