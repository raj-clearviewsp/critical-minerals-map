"""Pilot selection must preserve staging evidence and fail closed on bad helpers."""

import copy
import json
from pathlib import Path

import pyarrow as arrow
import pytest

from pipeline.normalize.core import make_row
from pipeline.pilot import core
from pipeline.vocab.core import Vocabulary


@pytest.fixture(scope="module")
def vocab():
    return Vocabulary(Path(__file__).resolve().parents[1])


def parent_row(source_id="icmm", **changes):
    file = f"{source_id}/2026-10-31/original.xlsx"
    raw = {
        "native_id": "publisher-record-17",
        "source_file": file,
        "source_table": "Source table",
        "row_number": 18,
        "record_kind": "facility",
        "raw_name": "Original name",
        "aliases": ["Original alias"],
        "lat": None,
        "lon": None,
        "country": "Zambia",
        "raw_commodities": {"Primary Commodity": "Nickel"},
        "raw_fields": {"Country": "Zambia", "Grade": "0.5(?)", "Tonnage": "1,000"},
        "raw_size_grade_text": {"Grade": "0.5(?)", "Tonnage": "1,000", "zero": 0},
        "reference": {"source": "Historical report", "year": 2011},
    }
    raw.update(changes)
    return make_row(
        {"source_id": source_id, "url": "https://publisher.example/data", "evidence_grade": "B"},
        {"retrieved_at": None, "registered_at": "2026-10-31T00:00:00+00:00"},
        {
            file: {
                "sha256": "a" * 64,
                "url": "https://publisher.example/original.xlsx",
                "retrieved_at": None,
            }
        },
        raw,
    )


def evidence(codes=None, **changes):
    result = {
        "field": "Primary Commodity",
        "raw_value": "Nickel",
        "codes": ["Ni"] if codes is None else codes,
        "role": "primary",
        "basis": "source_term",
        "qualified": False,
        "unmapped": [],
    }
    result.update(changes)
    return result


def country_result():
    return {
        "codes": ["ZMB"],
        "evidence": [
            {
                "field": "Country_of_Activity_ISO3",
                "location": "raw_fields",
                "raw_value": "ZMB",
                "role": "host_activity",
                "codes": ["ZMB"],
                "unresolved": [],
                "basis": "Exact ISO 3166-1 alpha-3",
                "assigns_country": True,
                "preferred": True,
            },
            {
                "field": "Financier_Country",
                "location": "raw_fields",
                "raw_value": "China",
                "role": "financier_origin",
                "codes": ["CHN"],
                "unresolved": [],
                "basis": "Exact ISO name",
                "assigns_country": False,
                "preferred": False,
            },
        ],
        "unresolved": [],
        "notes": ["Financier origin does not assign the host country."],
    }


def patch_helpers(monkeypatch, items, countries=None):
    monkeypatch.setattr(core, "commodity_evidence", lambda source, row, vocab: items)
    value = country_result() if countries is None else countries
    calls = []

    def country(source, row, vocab):
        calls.append((source, row["record_id"]))
        return value

    monkeypatch.setattr(core, "country_evidence", country)
    return calls


def test_selection_preserves_every_parent_field_and_raw_json(vocab, monkeypatch):
    parent = parent_row()
    original = copy.deepcopy(parent)
    items = [
        evidence(["Ni", "Cu"]),
        evidence(
            ["Co", "REE", "Ni"],
            field="Other Commodities",
            raw_value="Co, REE, Ni(?)",
            role="unspecified",
            qualified=True,
        ),
    ]
    calls = patch_helpers(monkeypatch, items)
    selected = core.select_row("icmm", parent, vocab)
    assert parent == original
    assert {key: selected[key] for key in original} == original
    assert json.loads(selected["pilot_commodities"]) == ["Co", "Ni", "REE"]
    assert json.loads(selected["pilot_families"]) == ["Co", "Ni", "REE"]
    assert json.loads(selected["pilot_evidence"]) == items
    assert selected["has_qualified_pilot_evidence"] is True
    assert calls == [("icmm", parent["record_id"])]
    assert selected["retrieved_at"] is None
    assert selected["raw_size_grade_text"] == original["raw_size_grade_text"]


@pytest.mark.parametrize("items", [[], [evidence(["Cu"])], [evidence(["Ni"], role="collateral")]])
def test_nonpilot_and_collateral_only_skip_country_work(vocab, monkeypatch, items):
    calls = patch_helpers(monkeypatch, items)
    assert core.select_row("icmm", parent_row(), vocab) is None
    assert calls == []


def test_partially_mapped_cell_is_retained_with_its_unmapped_evidence(vocab, monkeypatch):
    item = evidence(["Ni"], raw_value="Nickel, unreviewed mineral", unmapped=["unreviewed mineral"])
    patch_helpers(monkeypatch, [item])
    selected = core.select_row("icmm", parent_row(), vocab)
    assert json.loads(selected["pilot_evidence"]) == [item]
    assert json.loads(selected["pilot_commodities"]) == ["Ni"]


@pytest.mark.parametrize(
    "other",
    [
        evidence(["Cu"], qualified=True),
        evidence(["Co"], qualified=True, role="collateral"),
        evidence([], qualified=True, unmapped=["Nickel(?)"]),
    ],
)
def test_qualified_flag_only_describes_eligible_pilot_evidence(vocab, monkeypatch, other):
    patch_helpers(monkeypatch, [evidence(["Ni"]), other])
    selected = core.select_row("icmm", parent_row(), vocab)
    assert selected["has_qualified_pilot_evidence"] is False


def test_country_host_codes_and_financier_evidence_remain_separate(vocab, monkeypatch):
    countries = country_result()
    patch_helpers(monkeypatch, [evidence(["Ni"])], countries)
    selected = core.select_row("aiddata_transition_v2", parent_row("aiddata_transition_v2"), vocab)
    assert json.loads(selected["country_iso3s"]) == ["ZMB"]
    assert json.loads(selected["country_evidence"]) == countries
    assert selected["country"] == "Zambia"


@pytest.mark.parametrize(
    "item",
    [
        evidence(["NOT_A_CODE"]),
        evidence("Ni"),
        evidence(["Ni", 7]),
        evidence(["Ni", "Ni"]),
        evidence(qualified="false"),
        evidence(role="owner"),
        evidence(basis="keyword_guess"),
        evidence(raw_value=None),
        evidence(unmapped="unknown"),
    ],
)
def test_malformed_commodity_evidence_raises_before_country_work(vocab, monkeypatch, item):
    calls = patch_helpers(monkeypatch, [item])
    with pytest.raises(core.PilotError):
        core.select_row("icmm", parent_row(), vocab)
    assert calls == []


def test_commodity_helper_must_return_an_evidence_list(vocab, monkeypatch):
    patch_helpers(monkeypatch, {"codes": ["Ni"]})
    with pytest.raises(core.PilotError):
        core.select_row("icmm", parent_row(), vocab)


@pytest.mark.parametrize("codes", [["XKX"], "ZMB", ["ZMB", "ZMB"]])
def test_country_output_requires_unique_assigned_iso3_codes(vocab, monkeypatch, codes):
    countries = country_result()
    countries["codes"] = codes
    patch_helpers(monkeypatch, [evidence(["Ni"])], countries)
    with pytest.raises(core.PilotError):
        core.select_row("icmm", parent_row(), vocab)


def test_nonhost_country_evidence_cannot_claim_host_assignment(vocab, monkeypatch):
    countries = country_result()
    countries["evidence"][1]["assigns_country"] = True
    patch_helpers(monkeypatch, [evidence(["Ni"])], countries)
    with pytest.raises(core.PilotError):
        core.select_row("icmm", parent_row(), vocab)


def test_conflicting_host_sets_cannot_assign_either_country(vocab, monkeypatch):
    countries = country_result()
    countries["evidence"].append(
        dict(
            countries["evidence"][0],
            field="Country_of_Activity",
            raw_value="Chile",
            codes=["CHL"],
            preferred=False,
        )
    )
    countries["unresolved"] = ["ZMB", "Chile"]
    # The malformed helper keeps ZMB assigned despite a second valid host set.
    patch_helpers(monkeypatch, [evidence(["Ni"])], countries)
    with pytest.raises(core.PilotError, match="unambiguous host/activity"):
        core.select_row("icmm", parent_row(), vocab)


def test_unresolved_preferred_host_prevents_fallback_assignment(vocab, monkeypatch):
    countries = country_result()
    countries["evidence"].append(
        dict(
            countries["evidence"][0],
            field="Country_of_Activity",
            raw_value="Zambia",
            preferred=False,
        )
    )
    countries["evidence"][0].update(raw_value="ZZZ", codes=[], unresolved=["ZZZ"])
    countries["unresolved"] = ["ZZZ"]
    # A valid fallback name cannot override an unresolved preferred ISO field.
    patch_helpers(monkeypatch, [evidence(["Ni"])], countries)
    with pytest.raises(core.PilotError, match="unambiguous host/activity"):
        core.select_row("icmm", parent_row(), vocab)


def test_parent_source_identity_must_match_dispatch(vocab, monkeypatch):
    patch_helpers(monkeypatch, [evidence(["Ni"])])
    with pytest.raises(core.PilotError):
        core.select_row("wapha", parent_row("icmm"), vocab)


def test_batch_schema_keeps_qualification_boolean(vocab, monkeypatch):
    patch_helpers(monkeypatch, [evidence(["Ni"], qualified=True)])
    selected = core.select_row("icmm", parent_row(), vocab)
    table = core.validate_batch([selected], vocab=vocab)
    assert table.num_rows == 1
    assert table.schema.field("has_qualified_pilot_evidence").type == arrow.bool_()
    assert table.to_pylist()[0]["has_qualified_pilot_evidence"] is True
    malformed = dict(selected, has_qualified_pilot_evidence="false")
    with pytest.raises(core.PilotError):
        core.validate_batch([malformed], vocab=vocab)
