"""Review routing must not turn shared context or incomplete evidence into identity."""

import copy
import math

import pytest

from pipeline.link.candidates import EARTH_RADIUS_KM, haversine_km
from pipeline.triage.rules import TriageRuleError, triage_pairs


def record(identity, name="Borealis", *, distance=0, **overrides):
    return {
        "record_id": identity,
        "source_id": "source_" + identity,
        "primary_name": name,
        "display_name": name,
        "identity_aliases": [],
        "context_names": [],
        "unverified_aliases": [],
        "entity_level": "site",
        "scope_flags": [],
        "deposit_types": [],
        "country_iso3s": ["AUS"],
        "pilot_families": ["Ni"],
        "location_precision": None,
        "lat": 0.0,
        "lon": 30 + math.degrees(distance / EARTH_RADIUS_KM),
        **overrides,
    }


def candidate(left, right, identity="pair_example"):
    return {
        "pair_id": identity,
        "left_record_id": left["record_id"],
        "right_record_id": right["record_id"],
        "left_source_id": left["source_id"],
        "right_source_id": right["source_id"],
        "shared_families": sorted(set(left["pilot_families"]) & set(right["pilot_families"])),
        "distance_km": haversine_km(left["lat"], left["lon"], right["lat"], right["lon"]),
        "radius_km": 10,
        "name_category": "old category retained verbatim",
    }


def routed(left, right, *, extra=(), source_views=None):
    records = {r["record_id"]: r for r in [left, right, *extra]}
    return triage_pairs([candidate(left, right)], records, source_views or {"pairs": []})[0]


def test_close_exact_distinctive_name_routes_to_audit_without_claiming_a_match():
    pair = routed(record("a", "Borealis Mine"), record("b", "Borealis", distance=0.1))
    assert pair["queue"] == "likely_match"
    assert pair["disposition"] == "audit_likely_match"
    assert pair["features"]["trusted_exact_name"] is True
    assert pair["features"]["distinctive_shared_tokens"] == ["borealis"]
    assert pair["features"]["deposit_type_comparison"] == "missing_on_one_or_both"
    assert pair["auto_merge_eligible"] is False
    assert "label" not in pair and "match_probability" not in pair


def test_distant_unrelated_name_is_low_priority_not_a_confirmed_nonmatch():
    pair = routed(record("a", "Borealis"), record("b", "Ziranda", distance=6))
    assert pair["queue"] == "likely_distinct"
    assert pair["disposition"] == "audit_likely_distinct"
    assert "does not establish" in pair["explanation"]
    assert pair["auto_merge_eligible"] is False


def test_unknown_scope_is_neutral_for_low_priority_routing():
    pair = routed(
        record("a", "Borealis", entity_level="unknown", scope_flags=["entity_level_unresolved"]),
        record(
            "b",
            "Ziranda",
            distance=6,
            entity_level="unknown",
            scope_flags=["entity_level_unresolved"],
        ),
    )
    assert pair["queue"] == "likely_distinct"
    assert "individual_site_scope_unconfirmed" in pair["reason_codes"]
    assert pair["auto_merge_eligible"] is False


def test_unknown_scope_does_not_hide_numeric_conflicts():
    pair = routed(
        record(
            "a",
            "No. 53 Thorium Deposit",
            entity_level="unknown",
            scope_flags=["entity_level_unresolved"],
        ),
        record(
            "b",
            "Thorium Deposit No. 69",
            distance=1.5,
            entity_level="unknown",
            scope_flags=["entity_level_unresolved"],
        ),
    )
    assert "different_numbered_identifiers" in pair["reason_codes"]
    assert pair["disposition"] == "identifier_conflict_review"


@pytest.mark.parametrize("name", ["Unknown", "Unnamed Mine", "No Alternate Name", "N/A", "???"])
def test_exact_placeholder_names_never_become_strong_identity(name):
    pair = routed(record("a", name), record("b", name, distance=0.01))
    assert pair["queue"] == "ambiguous"
    assert not pair["features"]["trusted_exact_name"]
    assert pair["features"]["meaningful_name_missing"]


def test_explicit_unusable_primary_name_cannot_become_strong_identity():
    pair = routed(
        record("a", "Unnamed location 7", primary_name_usable=False),
        record("b", "Unnamed location 7", distance=0.01, primary_name_usable=False),
    )
    assert not pair["features"]["trusted_exact_name"]
    assert pair["queue"] != "likely_match"


def test_generic_mineral_words_do_not_make_nearby_numbered_deposits_identical():
    pair = routed(
        record("a", "No. 53 Thorium Deposit"), record("b", "Thorium Deposit No. 69", distance=1.5)
    )
    assert pair["queue"] == "ambiguous"
    assert "different_numbered_identifiers" in pair["reason_codes"]
    assert pair["features"]["shared_name_tokens"] == []
    assert pair["features"]["numeric_conflict"] is True


def test_common_number_does_not_hide_another_identifier_conflict():
    pair = routed(
        record("a", "Borealis No 53 Zone 1"), record("b", "Borealis No 69 Zone 1", distance=0.1)
    )
    assert pair["features"]["numeric_conflict"] is True
    assert pair["queue"] == "ambiguous"


def test_north_and_south_remain_distinguishing_identifiers():
    pair = routed(record("a", "North Borealis"), record("b", "South Borealis", distance=0.1))
    assert pair["queue"] == "ambiguous"
    assert "opposing_directional_identifiers" in pair["reason_codes"]


def test_only_one_direction_is_uncertainty_even_when_an_alias_agrees():
    pair = routed(
        record("a", "North Borealis", identity_aliases=["Borealis"]),
        record("b", "Borealis", distance=0.1),
    )
    assert pair["features"]["trusted_exact_name"] is True
    assert pair["queue"] == "ambiguous"
    assert "directional_qualifier_difference" in pair["reason_codes"]


def test_icmm_group_names_are_context_not_identity_aliases():
    pair = routed(
        record("a", "Kamoto", context_names=["Kolwezi"]),
        record("b", "Kolwezi (Commus)", context_names=["Kolwezi"], distance=6),
    )
    assert pair["features"]["context_exact_names"] == ["kolwezi"]
    assert not pair["features"]["trusted_exact_name"]
    assert pair["queue"] == "ambiguous"
    assert "weak_alias_or_context_agreement" in pair["reason_codes"]


def test_mrds_unverified_shared_area_name_rescues_review_but_never_implies_identity():
    pair = routed(
        record("a", "Lein 2 Smith Meyers Mine", unverified_aliases=["Hosanger Gruber"]),
        record("b", "Nonaas Grube", unverified_aliases=["Hosanger Gruber"], distance=3),
    )
    assert pair["queue"] == "ambiguous"
    assert pair["features"]["unverified_exact_names"] == ["hosanger gruber"]
    assert not pair["features"]["trusted_exact_name"]


def test_broad_region_and_site_enter_relationship_review_without_asserting_contains():
    pair = routed(
        record("a", "Capel", entity_level="district_area"), record("b", "North Capel", distance=5)
    )
    assert pair["queue"] == "relationship"
    assert pair["requires_adjudication"] is True
    assert "direction has not been asserted" in pair["explanation"]
    assert "relationship_direction" not in pair


def test_two_complex_records_are_not_treated_as_individual_site_matches():
    pair = routed(
        record("a", "Borealis", entity_level="operation_complex"),
        record("b", "Borealis", entity_level="operation_complex", distance=0.01),
    )
    assert pair["queue"] == "relationship"


def test_scope_flags_prevent_likely_match_and_remain_in_features():
    pair = routed(
        record("a", "Tocantins", scope_flags=["name_suggests_processing_facility"]),
        record("b", "Tocantins", distance=0.01),
    )
    assert pair["queue"] == "ambiguous"
    assert pair["features"]["left_scope_flags"] == ["name_suggests_processing_facility"]


def test_unknown_scope_does_not_receive_likely_match_routing():
    pair = routed(record("a", entity_level="unknown"), record("b", distance=0.01))
    assert pair["queue"] == "ambiguous"
    assert "individual_site_scope_unconfirmed" in pair["reason_codes"]


def test_coordinate_reuse_for_different_names_blocks_close_name_match():
    pair = routed(
        record("a", "Borealis"),
        record("b", "Borealis", distance=0.1),
        extra=[record("c", "Other distinct mine")],
    )
    assert pair["queue"] == "ambiguous"
    assert pair["features"]["left_coordinate_count"] == 2
    assert pair["features"]["left_coordinate_name_count"] == 2
    assert "coordinates_reused_for_different_names" in pair["reason_codes"]


def test_colocation_of_unrelated_shoot_names_is_not_identity():
    pair = routed(record("a", "Durkin Shoot"), record("b", "Otter Shoot"))
    assert pair["queue"] == "ambiguous"
    assert pair["features"]["coordinate_reuse"] is True
    assert pair["features"]["shared_name_tokens"] == []


def test_geology_missing_is_neutral_but_different_reported_types_need_review():
    pair = routed(
        record("a", deposit_types=["laterite"]),
        record("b", deposit_types=["magmatic_sulfide"], distance=0.1),
    )
    assert pair["queue"] == "ambiguous"
    assert "reported_deposit_types_differ" in pair["reason_codes"]
    blank = routed(
        record("a", deposit_types=["laterite"]),
        record("b", deposit_types=["unknown"], distance=0.1),
    )
    assert blank["queue"] == "likely_match"


def test_status_history_never_changes_identity_routing():
    first, second = (
        record("a", raw_status={"status": "Operating"}),
        record("b", distance=0.1, raw_status={"status": "Closed"}),
    )
    assert routed(first, second)["queue"] == "likely_match"


def test_country_disagreement_is_visible_without_being_a_rejection_rule():
    pair = routed(record("a"), record("b", distance=0.1, country_iso3s=["CAN"]))
    assert pair["queue"] == "ambiguous"
    assert "reported_country_disagreement" in pair["reason_codes"]


def test_close_spelling_variant_prevents_distant_low_priority_routing():
    pair = routed(record("a", "Prrenjas"), record("b", "Prrenjass", distance=4))
    assert pair["queue"] == "ambiguous"
    assert pair["features"]["max_character_similarity"] > 0.82


def test_generic_only_name_overlap_does_not_rescue_distant_pairs():
    pair = routed(
        record("a", "Hardy Nickel Sulfide Mine"),
        record("b", "Buck Nickel Sulfide Mine", distance=4),
    )
    assert pair["features"]["shared_name_tokens"] == []
    assert pair["features"]["max_character_similarity"] == 0
    assert pair["queue"] == "likely_distinct"


def test_common_tokens_get_less_weight_than_distinctive_tokens():
    a = record("a", "Borealis Shared")
    b = record("b", "Borealis Separate", distance=0.1)
    extra = [record(f"x{n}", f"Shared Otherword{n}", distance=10 + n) for n in range(20)]
    pair = routed(a, b, extra=extra)
    assert pair["features"]["shared_name_tokens"] == ["borealis"]
    assert pair["features"]["distinctive_shared_tokens"] == ["borealis"]
    assert pair["features"]["weighted_name_overlap"] > 1 / 3


def test_source_views_are_reconciliation_not_independent_site_evidence():
    a, b = record("a"), record("b")
    views = {
        "pairs": [
            {
                "left_record_id": "a",
                "right_record_id": "b",
                "reason": "same publisher release",
                "evidence": {"fields": "agree"},
            }
        ]
    }
    pair = routed(a, b, source_views=views)
    assert pair["queue"] == "likely_match"
    assert pair["disposition"] == "source_view_duplicate"
    assert pair["auto_merge_eligible"] is False
    assert "not independent corroboration" in pair["explanation"]


def test_inputs_are_unmodified_and_order_does_not_change_outputs():
    a, b, c = record("a"), record("b", distance=0.1), record("c", "Ziranda", distance=5)
    records = {r["record_id"]: r for r in [a, b, c]}
    candidates = [candidate(a, b, "pair_2"), candidate(a, c, "pair_1")]
    before = copy.deepcopy((records, candidates))
    result = triage_pairs(candidates, records, {"pairs": []})
    reverse = triage_pairs(
        list(reversed(candidates)), dict(reversed(list(records.items()))), {"pairs": []}
    )
    assert result == reverse
    assert (records, candidates) == before
    assert [r["pair_id"] for r in result] == ["pair_1", "pair_2"]
    assert all(r["name_category"] == "old category retained verbatim" for r in result)


@pytest.mark.parametrize("coordinate", [None, float("nan"), float("inf"), True, 91])
def test_bad_coordinates_fail_instead_of_silent_routing(coordinate):
    a, b = record("a"), record("b", lat=coordinate)
    with pytest.raises(TriageRuleError):
        triage_pairs([candidate(a, a)], {"a": a, "b": b}, {"pairs": []})


def test_malformed_or_changed_candidate_fails():
    a, b = record("a"), record("b", distance=0.1)
    original = candidate(a, b)
    for change in (
        {"distance_km": 5},
        {"shared_families": ["Co"]},
        {"right_record_id": "missing"},
        {"label": "same_site"},
    ):
        with pytest.raises(TriageRuleError):
            triage_pairs([{**original, **change}], {"a": a, "b": b}, {"pairs": []})
    with pytest.raises(TriageRuleError, match="Repeated"):
        triage_pairs([original, original], {"a": a, "b": b}, {"pairs": []})
