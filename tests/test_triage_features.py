"""Entity-grain and source-view features must not invent site identity."""

import copy
import json
from pathlib import Path

import pytest

from pipeline.triage.features import (
    GA_RELEASE,
    GA_WORKBOOK,
    enrich_record,
    is_placeholder_name,
    reconcile_source_views,
)
from pipeline.vocab.core import Vocabulary


@pytest.fixture(scope="module")
def vocab():
    return Vocabulary(Path(__file__).resolve().parents[1])


def row(source="usgs_mrds", **changes):
    result = {
        "record_id": source + ":1",
        "source_id": source,
        "native_id": "native-id",
        "raw_name": "Example Mine",
        "record_kind": "deposit",
        "lat": -20.0,
        "lon": 140.0,
        "location_precision": None,
        "country": "Australia",
        "country_iso3s": '["AUS"]',
        "pilot_families": '["Ni"]',
        "pilot_commodities": '["Ni"]',
        "pilot_evidence": "[]",
        "raw_fields": "{}",
        "aliases": "[]",
        "raw_deposit_type": "{}",
        "raw_status": "{}",
        "raw_commodities": "{}",
        "reference": "{}",
        "source_url": "https://example.test/original.zip",
        "source_currency": "2025-12",
        "source_file": "source/2026-10-31/raw.zip",
        "source_table": "records",
        "source_row": 2,
        "raw_sha256": "a" * 64,
        "retrieved_at": None,
        "registered_at": "2026-10-31T00:00:00Z",
        "evidence_grade": "B",
    }
    result.update(changes)
    return result


@pytest.mark.parametrize(
    "term,level,qualified",
    [
        ("site", "site", False),
        ("site(?)", "site", True),
        ("district or area", "district_area", False),
        ("district or area(?)", "district_area", True),
        ("intrusion or complex", "operation_complex", False),
        ("intrusion or complex(?)", "operation_complex", True),
    ],
)
def test_explicit_ree_granularity_preserves_uncertainty(vocab, term, level, qualified):
    original = row("usgs_global_ree", raw_fields=json.dumps({"Rec_Type": term}))
    before = copy.deepcopy(original)
    result = enrich_record(original, vocab)
    assert result["entity_level"] == level
    assert result["entity_level_evidence"][0]["raw_value"] == term
    assert result["entity_level_evidence"][0]["qualified"] == qualified
    assert ("qualified_entity_level" in result["scope_flags"]) == qualified
    assert result["location_precision"] is None
    assert original == before


def test_jasansky_subsite_displaces_parent_only_in_display(vocab):
    original = row(
        "jasansky",
        raw_name="Nornickel Taimyr Peninsula",
        aliases='["Old parent name"]',
        raw_fields=json.dumps(
            {
                "facility_name": "Nornickel Taimyr Peninsula",
                "sub_site_name": "Zapolyarny",
                "facility_other_names": "Polar operations",
                "sub_site_other_names": "Historic Zapolyarny",
                "facility_type": "Mine",
            }
        ),
    )
    result = enrich_record(original, vocab)
    assert result["primary_name"] == result["display_name"] == "Zapolyarny"
    assert result["parent_name"] == result["raw_name"] == "Nornickel Taimyr Peninsula"
    assert result["entity_level"] == "subsite"
    assert result["identity_aliases"] == ["Historic Zapolyarny"]
    assert result["context_names"] == ["Nornickel Taimyr Peninsula", "Polar operations"]
    assert result["aliases_raw"] == ["Old parent name"]


def test_icmm_group_membership_never_becomes_identity_aliases_or_crosswalk(vocab):
    result = enrich_record(
        row(
            "icmm",
            raw_name="Kamoto",
            aliases='["Kamoto;Kolwezi"]',
            raw_fields=json.dumps(
                {
                    "Group Names": "Kamoto; Kolwezi; Luilu refinery",
                    "Contains": "WA13778; JA585",
                    "Asset Type": "Mine",
                }
            ),
        ),
        vocab,
    )
    assert result["identity_aliases"] == result["unverified_aliases"] == []
    assert result["context_names"] == ["Kamoto", "Kolwezi", "Luilu refinery"]
    assert result["entity_level"] == "unknown"
    assert "publisher_cluster_record" in result["scope_flags"]
    assert result["source_view"] is None
    assert {n["field"]: n["raw_value"] for n in result["source_notes"]}[
        "Contains"
    ] == "WA13778; JA585"


def test_mrds_alternates_are_unverified_and_direct_record_link_is_preferred(vocab):
    url = "https://mrdata.usgs.gov/mrds/show-mrds.php?dep_id=10038231"
    result = enrich_record(
        row(
            raw_name="Lein 2 Smith Meyers Mine",
            aliases='["Lein 2 Smith Meyers Mine","Hosanger Gruber","No Alternate Name"]',
            reference=json.dumps({"record_url": url}),
        ),
        vocab,
    )
    assert result["identity_aliases"] == []
    assert result["unverified_aliases"] == ["Lein 2 Smith Meyers Mine", "Hosanger Gruber"]
    assert result["excluded_name_placeholders"] == ["No Alternate Name"]
    assert result["site_url"] == url
    assert result["source_links"][0]["kind"] == "record"


@pytest.mark.parametrize(
    "name",
    [
        None,
        "",
        "Unnamed",
        "Unknown",
        "Name Unknown",
        "No Alternate Name",
        "Unnamed occurrence 05435",
    ],
)
def test_placeholder_names_do_not_supply_strong_identity(name):
    assert is_placeholder_name(name)


@pytest.mark.parametrize("name", ["No Name Creek", "No. 53 Thorium Deposit", "North Capel Mine"])
def test_distinctive_numbers_and_directions_survive(vocab, name):
    assert not is_placeholder_name(name)
    result = enrich_record(row(raw_name=name), vocab)
    assert result["primary_name"] == name and result["primary_name_usable"]


def test_name_hints_do_not_assert_publisher_type(vocab):
    processing = enrich_record(row("wapha", raw_name="Tocantins Leach Plant"), vocab)
    broad = enrich_record(row(raw_name="Rock Run District"), vocab)
    assert processing["entity_level"] == broad["entity_level"] == "unknown"
    assert "name_suggests_processing_facility" in processing["scope_flags"]
    assert "name_suggests_broad_scope" in broad["scope_flags"]


def test_geology_uses_declared_type_only_and_retains_qualified_raw(vocab):
    result = enrich_record(
        row(
            "usgs_global_ree",
            raw_deposit_type=json.dumps(
                {
                    "Dep_Type": "carbonatite(?)",
                    "Rec_Type": "district or area",
                    "Dep_Form": "vein",
                }
            ),
        ),
        vocab,
    )
    assert result["deposit_types"] == ["carbonatite"]
    assert len(result["deposit_type_evidence"]) == 1
    assert result["deposit_type_evidence"][0]["qualified"]
    assert result["deposit_types_raw"]["Dep_Form"] == "vein"
    unknown = enrich_record(
        row("usgs_global_ree", raw_deposit_type='{"Dep_Type":"unknown"}'), vocab
    )
    assert unknown["deposit_types"] == []
    assert enrich_record(row(), vocab)["deposit_types"] == []


@pytest.mark.parametrize(
    "url",
    [
        "javascript:alert(1)",
        "https://user:secret@example.test/",
        "https://example.test/\nattack",
        "http://example.test/",
    ],
)
def test_unsafe_direct_links_are_retained_as_raw_but_not_clickable(vocab, url):
    result = enrich_record(row(reference=json.dumps({"record_url": url})), vocab)
    assert result["site_url"] is None
    assert result["reference"]["record_url"] == url
    assert len(result["source_links"]) == 1


def ga_pair(vocab, name="Mount Cobalt", commodity="Nickel +/- Cobalt", suffix="1"):
    fields = {"State": "QLD", "Status": "Mineral deposit", "Commodity Group": commodity}
    left = enrich_record(
        row(
            "ga_critical_2025",
            record_id="ga_critical_2025:" + suffix,
            raw_name=name,
            source_url=GA_WORKBOOK,
            raw_fields=json.dumps(fields),
        ),
        vocab,
    )
    fields = {
        "state": "QLD",
        "status": "Mineral deposit",
        "commodities": commodity,
        "source": "Australian Critical Minerals Map 2025, " + GA_RELEASE,
    }
    right = enrich_record(
        row(
            "ga_critical_2025_arcgis",
            record_id="ga_critical_2025_arcgis:" + suffix,
            raw_name=name,
            raw_fields=json.dumps(fields),
        ),
        vocab,
    )
    return left, right


def test_ga_views_require_unique_matching_full_signature(vocab):
    pair = ga_pair(vocab)
    second = ga_pair(vocab, commodity="Cobalt", suffix="2")
    records = [*pair, *second]
    before = copy.deepcopy(records)
    result = reconcile_source_views(records)
    assert len(result["pairs"]) == 2 and not result["unmatched"]
    assert result == reconcile_source_views(list(reversed(records)))
    assert records == before
    assert all(p["reason"] == "same_publisher_release_view" for p in result["pairs"])
    assert result["pairs"][0]["evidence"]["publisher_release"] == GA_RELEASE


@pytest.mark.parametrize(
    "key,value",
    [
        ("status", "Operating mine"),
        ("currency", "2026-12"),
        ("state", "WA"),
        ("commodity_group", "Nickel"),
        ("publisher_release", None),
    ],
)
def test_ga_different_release_or_classification_does_not_reconcile(vocab, key, value):
    left, right = ga_pair(vocab)
    right["source_view"][key] = value
    result = reconcile_source_views([left, right])
    assert not result["pairs"] and len(result["unmatched"]) == 2


@pytest.mark.parametrize(
    "changes",
    [
        {"lat": -21},
        {"lat": float("nan")},
        {"lat": True},
        {"lon": 181},
        {"country_iso3s": []},
        {"pilot_commodities": ["Co"]},
        {"record_kind": "facility"},
    ],
)
def test_ga_different_point_or_scope_does_not_reconcile(vocab, changes):
    left, right = ga_pair(vocab)
    right.update(changes)
    assert not reconcile_source_views([left, right])["pairs"]


def test_ga_repeated_signature_is_ambiguous_and_duplicate_record_id_fails(vocab):
    left, right = ga_pair(vocab)
    repeated = copy.deepcopy(left)
    repeated["record_id"] += "-second"
    assert not reconcile_source_views([left, right, repeated])["pairs"]
    with pytest.raises(ValueError, match="Duplicate source record"):
        reconcile_source_views([left, right, left])


def test_ga_float_rounding_is_allowed_but_not_metre_scale_differences(vocab):
    left, right = ga_pair(vocab)
    right["lat"] += 1e-9
    assert len(reconcile_source_views([left, right])["pairs"]) == 1
    right["lat"] += 1e-5
    assert not reconcile_source_views([left, right])["pairs"]
