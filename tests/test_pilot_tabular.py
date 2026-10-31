"""Pilot evidence retains source roles, qualifiers, and unmapped tokens."""

import json
from pathlib import Path

import pytest

from pipeline.pilot.tabular import commodity_evidence
from pipeline.vocab.core import Vocabulary


@pytest.fixture(scope="module")
def vocab():
    return Vocabulary(Path(__file__).resolve().parents[1])


def record(commodities, raw_fields=None):
    return {"raw_commodities": json.dumps(commodities), "raw_fields": json.dumps(raw_fields or {})}


def eligible_codes(evidence):
    return {code for entry in evidence if entry["role"] != "collateral" for code in entry["codes"]}


def test_explicit_primary_secondary_roles_never_imply_byproduct(vocab):
    evidence = commodity_evidence(
        "icmm",
        record(
            {
                "Primary Commodity": "copper",
                "Secondary Commodity ": "nickel",
                "Other Commodities": "cobalt",
            }
        ),
        vocab,
    )
    roles = {e["field"]: e["role"] for e in evidence}
    assert roles == {
        "Primary Commodity": "primary",
        "Secondary Commodity ": "secondary",
        "Other Commodities": "unspecified",
    }
    assert eligible_codes(evidence) == {"Cu", "Ni", "Co"}
    assert not any(e["role"] == "byproduct" for e in evidence)


def test_ga_possible_group_retains_whole_cell_and_qualification(vocab):
    raw = "Nickel +/- Cobalt +/- PGE"
    for source, field in [
        ("ga_critical_2025", "Commodity Group"),
        ("ga_critical_2025_arcgis", "commodities"),
    ]:
        evidence = commodity_evidence(source, record({field: raw}), vocab)
        assert len(evidence) == 1
        assert evidence[0]["codes"] == ["Co", "Ni", "PGE"]
        assert evidence[0]["qualified"] is True and evidence[0]["raw_value"] == raw
        assert evidence[0]["role"] == "unspecified"


def test_unknown_qualified_term_is_not_silently_rewritten(vocab):
    evidence = commodity_evidence("wapha", record({"Commodity": "Ni(?)"}), vocab)
    assert evidence[0]["qualified"] is True
    assert evidence[0]["codes"] == [] and evidence[0]["unmapped"] == ["Ni(?)"]


def test_partial_mapping_preserves_unmapped_tokens_in_same_evidence(vocab):
    evidence = commodity_evidence("wapha", record({"Commodity": "Nickel, mystery mineral"}), vocab)
    assert len(evidence) == 1
    assert evidence[0]["codes"] == ["Ni"]
    assert evidence[0]["unmapped"] == ["mystery mineral"]
    assert evidence[0]["raw_value"] == "Nickel, mystery mineral"


def test_ga_ree_only_documented_scope_not_tectonic_element(vocab):
    evidence = commodity_evidence(
        "ga_global_ree",
        record({"source_scope": "REE"}, {"Element": "Nickel", "Ni (ppm)": 0}),
        vocab,
    )
    assert evidence == [
        {
            "field": "source_scope",
            "raw_value": "REE",
            "codes": ["REE"],
            "role": "unspecified",
            "basis": "source_scope",
            "qualified": False,
            "unmapped": [],
        }
    ]
    other = commodity_evidence("ga_global_ree", record({"source_scope": "Nickel"}), vocab)
    assert other[0]["codes"] == []


def test_oecd_uses_positive_hs_code_only_not_exclusion_text(vocab):
    positive = commodity_evidence("oecd_export", record({"COMMODITY": "HS07_260400"}), vocab)
    assert positive[0]["codes"] == ["Ni"]
    for code in ["HS07_999999", "Nickel", "Copper (excluding nickel)"]:
        evidence = commodity_evidence(
            "oecd_export",
            record({"COMMODITY": code}, {"POLICY_DESCRIPTION": "Nickel, cobalt and rare earths"}),
            vocab,
        )
        assert eligible_codes(evidence) == set()
        assert evidence[0]["unmapped"] == [code]


def test_aiddata_collateral_cannot_supply_asset_pilot_association(vocab):
    evidence = commodity_evidence(
        "aiddata_transition_v2",
        record({"Transition_Minerals": "Copper", "Commodity": "Nickel|Cobalt"}),
        vocab,
    )
    assert eligible_codes(evidence) == {"Cu"}
    collateral = next(e for e in evidence if e["field"] == "Commodity")
    assert collateral["role"] == "collateral" and collateral["codes"] == ["Co", "Ni"]
    evidence = commodity_evidence(
        "aiddata_transition_v2",
        record(
            {"Primary_Minerals": "Nickel", "Subsidiary_Minerals": "Cobalt", "Commodity": "Copper"}
        ),
        vocab,
    )
    assert eligible_codes(evidence) == {"Ni", "Co"}
    assert {e["role"] for e in evidence} == {"primary", "secondary", "collateral"}


def child(facility_id, **fields):
    return {
        "source_table": "data/processing.csv",
        "row_number": 17,
        "raw_fields": {"facility_id": facility_id, **fields},
    }


def test_jasansky_same_facility_child_products_have_reviewable_paths(vocab):
    evidence = commodity_evidence(
        "jasansky",
        record(
            {"primary_commodity": "Copper"},
            {
                "facility_id": "F.01",
                "_linked_tables": {
                    "data/processing.csv": [child("F.01", input="Me.Ni", output="Nickel in matte")],
                    "data/ownership.csv": [
                        child("F.01", owners="Nickel Cobalt Limited", commodity="Me.Co")
                    ],
                },
            },
        ),
        vocab,
    )
    assert eligible_codes(evidence) == {"Cu", "Ni"}
    children = [e for e in evidence if "source_path" in e]
    assert len(children) == 2
    assert children[0]["field"] == "input"
    assert (
        children[0]["source_path"]
        == "/raw_fields/_linked_tables/data~1processing.csv/0/raw_fields/input"
    )
    assert all(e["role"] == "unspecified" for e in children)


def test_jasansky_foreign_facility_link_fails(vocab):
    with pytest.raises(ValueError, match="another or unknown facility"):
        commodity_evidence(
            "jasansky",
            record(
                {},
                {
                    "facility_id": "F.01",
                    "_linked_tables": {
                        "data/commodities.csv": [child("F.02", commodity="Me.Ni")],
                    },
                },
            ),
            vocab,
        )


def test_jasansky_nickel_in_waste_does_not_qualify_a_gold_facility(vocab):
    waste = child("F.01", material="Me.Ni", commodity="Me.Ni")
    waste["source_table"] = "data/waste.csv"
    evidence = commodity_evidence(
        "jasansky",
        record(
            {"primary_commodity": "Gold"},
            {"facility_id": "F.01", "_linked_tables": {"data/waste.csv": [waste]}},
        ),
        vocab,
    )
    assert eligible_codes(evidence) == {"Au"}
    assert not eligible_codes(evidence) & vocab.pilot_codes
    assert all("waste" not in entry.get("source_path", "") for entry in evidence)


def test_gseu_and_mcs_use_terms_not_numeric_measurement_inference(vocab):
    evidence = commodity_evidence(
        "gseu_crm",
        record({"CRM": "Ni", "Type of commodity reported": "Ni"}, {"Co grade": 100}),
        vocab,
    )
    assert eligible_codes(evidence) == {"Ni"}
    assert all(e["role"] == "unspecified" and e["basis"] == "source_term" for e in evidence)
    evidence = commodity_evidence("usgs_mcs_2026", record({"Commodity": "Cobalt"}), vocab)
    assert eligible_codes(evidence) == {"Co"}


def test_blanks_skipped_unknown_fields_retained_for_review(vocab):
    evidence = commodity_evidence(
        "icmm",
        record({"Primary Commodity": None, "Secondary Commodity ": " ", "New field": "Nickel"}),
        vocab,
    )
    assert len(evidence) == 1
    assert evidence[0]["codes"] == [] and evidence[0]["unmapped"] == ["Nickel"]


def test_json_shape_and_source_scope_fail_closed(vocab):
    with pytest.raises(ValueError, match="JSON object"):
        commodity_evidence("wapha", {"raw_commodities": '["Ni"]'}, vocab)
    with pytest.raises(ValueError, match="Unsupported"):
        commodity_evidence("usgs_mrds", record({"commod": "Nickel"}), vocab)
