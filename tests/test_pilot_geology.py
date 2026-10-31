"""Pilot associations must follow source fields and retain uncertainty."""

import json
from pathlib import Path

import pytest

from pipeline.pilot.geology import _pointer, commodity_evidence
from pipeline.vocab.core import Vocabulary


@pytest.fixture(scope="module")
def vocab():
    return Vocabulary(Path(__file__).resolve().parents[1])


def record(commodities, **fields):
    return {"raw_commodities": json.dumps(commodities), **fields}


def codes(evidence):
    return {code for item in evidence for code in item["codes"]}


def test_ree_qualified_composites_and_unknown_words_are_preserved(vocab):
    raw = "REE(?), Sn REE, unreviewed material"
    result = commodity_evidence("usgs_global_ree", record({"Commods": raw}), vocab)
    assert result[0]["raw_value"] == raw
    assert result[0]["qualified"]
    assert codes(result) == {"REE", "Sn"}
    assert result[0]["unmapped"] == ["unreviewed material"]
    assert result[0]["role"] == "unspecified"


def test_ree_scope_is_only_a_blank_commodity_fallback(vocab):
    result = commodity_evidence("usgs_global_ree", record({"Commods": " "}), vocab)
    assert codes(result) == {"REE"}
    assert result[0]["basis"] == "source_scope"
    explicit = commodity_evidence("usgs_global_ree", record({"Commods": "Re"}), vocab)
    assert codes(explicit) == {"Re"}
    assert not codes(explicit) & vocab.pilot_codes
    unknown = commodity_evidence("usgs_global_ree", record({"Commods": "new mineral"}), vocab)
    assert unknown[0]["codes"] == []
    assert unknown[0]["basis"] == "source_term"


def test_global_and_us_use_different_delimiters_without_role_inference(vocab):
    global_row = record({"CRITICAL_MINERAL": "Cobalt; Niobium and Tantalum"})
    result = commodity_evidence("usgs_global_critical", global_row, vocab)
    assert codes(result) == {"Co", "Nb", "Ta"}
    us_row = record({"critmin": "cobalt, nickel"}, raw_fields='{"rank":4,"name":"REE mine"}')
    result = commodity_evidence("usgs_us_critical", us_row, vocab)
    assert codes(result) == {"Co", "Ni"}
    assert all(item["role"] == "unspecified" for item in result)
    assert commodity_evidence("usgs_us_critical", record({"critmin": ""}), vocab) == []


@pytest.mark.parametrize("value", ["", "0", "0.0", "n.d.", None])
def test_laterite_blank_zero_or_missing_cobalt_does_not_assert_presence(vocab, value):
    result = commodity_evidence("usgs_laterites", record({"Co grade": value}), vocab)
    assert codes(result) == {"Ni"}
    assert result[0]["basis"] == "source_scope"


def test_laterite_positive_cobalt_grade_is_association_not_production_or_byproduct(vocab):
    result = commodity_evidence(
        "usgs_laterites", record({"Co grade": "0.08", "Ni grade": "1.2"}), vocab
    )
    assert codes(result) == {"Ni", "Co"}
    assert {item["basis"] for item in result} == {"column_header"}
    assert {item["role"] for item in result} == {"unspecified"}
    assert next(item for item in result if item["field"] == "Co grade")["raw_value"] == "0.08"


def test_laterite_upper_bound_and_unreviewed_column_are_visible_not_presence(vocab):
    result = commodity_evidence(
        "usgs_laterites", record({"Co grade": "<0.1", "New Co column": "0.2"}), vocab
    )
    assert codes(result) == {"Ni"}
    assert result[0]["qualified"] and result[0]["unmapped"] == ["<0.1"]
    assert result[1]["codes"] == [] and result[1]["unmapped"] == ["New Co column"]


def test_mrds_native_codes_child_roles_and_atomic_comma_names(vocab):
    children = [
        {"code": "NI_R", "commod": "Nickel, Refiner", "import": "Primary"},
        {"code": "CO", "commod": "Cobalt", "import": "Tertiary"},
        {"code": "REE", "commod": "Rare Earths", "import": "Secondary"},
    ]
    result = commodity_evidence(
        "usgs_mrds",
        record(
            {"code_list": " RE CO2 NI_R ", "Commodity.txt": children + [children[0]]},
            raw_fields=json.dumps({"name": "REE name", "Commodity dictionary": ["SC"]}),
        ),
        vocab,
    )
    assert {"Ni", "Co", "REE", "Re"} <= codes(result)
    assert "Sc" not in codes(result)
    assert len(result) == len({(item["field"], item["raw_value"]) for item in result})
    assert next(item for item in result if item["raw_value"] == "NI_R")["role"] == "primary"
    assert next(item for item in result if item["raw_value"] == "REE")["role"] == "secondary"
    assert next(item for item in result if item["raw_value"] == "CO")["role"] == "tertiary"
    assert next(item for item in result if item["raw_value"] == "Nickel, Refiner")["codes"] == [
        "Ni"
    ]


def test_mrds_linked_rows_fallback_ignores_materials_and_names(vocab):
    raw = {
        "linked_tables": {
            "Commodity.txt": [{"values": {"code": "NI_L", "import": "Secondary"}}],
            "Names.txt": [{"values": {"name": "Cobalt mine"}}],
            "Materials.txt": [{"values": {"material": "Monazite"}}],
        }
    }
    result = commodity_evidence(
        "usgs_mrds", record({"code_list": ""}, raw_fields=json.dumps(raw)), vocab
    )
    assert codes(result) == {"Ni"}
    assert result[0]["role"] == "secondary"
    assert result[0]["source_path"] == "/raw_fields/linked_tables/Commodity.txt/0/values/code"


def test_repeated_mrds_nickel_preserves_distinct_source_roles_and_pointers(vocab):
    result = commodity_evidence(
        "usgs_mrds",
        record(
            {
                "Commodity.txt": [
                    {"code": "NI", "import": "Primary"},
                    {"code": "NI", "import": "Secondary"},
                    {"code": "NI", "import": "Tertiary"},
                    {"code": "NI", "import": "Primary"},
                ]
            }
        ),
        vocab,
    )
    assert len(result) == 3
    assert codes(result) == {"Ni"}
    assert [item["role"] for item in result] == ["primary", "secondary", "tertiary"]
    assert [item["source_path"] for item in result] == [
        f"/raw_commodities/Commodity.txt/{index}/code" for index in range(3)
    ]


def test_source_pointer_escapes_json_member_names():
    assert _pointer("raw_commodities", "table~/name", 0, "code") == (
        "/raw_commodities/table~0~1name/0/code"
    )


def test_source_mismatch_and_invalid_json_fail_visibly(vocab):
    with pytest.raises(ValueError, match="source_id"):
        commodity_evidence("usgs_mrds", record({}, source_id="usgs_us_critical"), vocab)
    with pytest.raises(ValueError, match="JSON object"):
        commodity_evidence("usgs_mrds", {"raw_commodities": "[]"}, vocab)
