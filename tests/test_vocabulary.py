"""Regressions for semantic errors that would silently change pilot coverage."""

from pathlib import Path

import pytest

from pipeline.vocab.audit import check, expected_families
from pipeline.vocab.core import Vocabulary, VocabularyError

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def vocab():
    return Vocabulary(ROOT)


@pytest.mark.parametrize(
    "source,field,raw,codes",
    [
        ("usgs_mrds", "code", "NI_L", {"Ni"}),
        ("usgs_mrds", "code", "RE", {"Re"}),
        ("usgs_mrds", "commod", "Nickel, Smelter", {"Ni"}),
        ("usgs_global_ree", "Commods", "REE(?)", {"REE"}),
        ("usgs_global_ree", "Commods", "Sn REE", {"Sn", "REE"}),
        ("gseu_crm", "Type of commodity reported", "RE2O3", {"REE"}),
        ("jasansky", "commodities_products", "Nickel-Cobalt Mixed Sulfide", {"Ni", "Co"}),
        ("jasansky", "material_id", "Me.Nd2O3", {"Nd"}),
        ("oecd_export", "COMMODITY", "HS07_284610", {"Ce"}),
        ("oecd_export", "COMMODITY", "HS07_260500", {"Co"}),
    ],
)
def test_reviewed_source_codes(vocab, source, field, raw, codes):
    result = vocab.resolve("commodity", source, field, raw)
    assert set(result["codes"]) == codes
    assert not result["unmapped"]


@pytest.mark.parametrize(
    "raw", ["HS07_261790", "HS07_720299", "HS07_740829", "HS07_740990", "HS07_811222"]
)
def test_hs_exclusion_text_does_not_include_pilot(vocab, raw):
    result = vocab.resolve("commodity", "oecd_export", "COMMODITY", raw)
    assert not set(result["codes"]) & vocab.pilot_codes


def test_unknown_fields_and_terms_fail_visibly(vocab):
    with pytest.raises(VocabularyError, match="field policy"):
        vocab.resolve("commodity", "icmm", "Company name", "Nickel")
    result = vocab.resolve("commodity", "icmm", "Primary Commodity", "new unreviewed mineral")
    assert result["codes"] == []
    assert result["unmapped"] == ["new unreviewed mineral"]


def test_stale_producer_and_activity_never_become_operating(vocab):
    assert vocab.resolve("status", "usgs_mrds", "dev_stat", "Producer")["codes"] == [
        "producer_reported"
    ]
    assert vocab.resolve("status", "wapha", "Status", "Active")["codes"] == ["active_reported"]
    assert not vocab.resolve("status", "aiddata_transition_v2", "Status", "Completion")["codes"]


def test_country_code_systems_do_not_cross(vocab):
    assert vocab.country("usgs_mrds", "fips", "fAF")["codes"] == []
    assert vocab.country("usgs_laterites", "CountryCode", "AS")["codes"] == []
    assert vocab.country("gseu_crm", "ISO3", "AUT")["codes"] == ["AUT"]
    assert vocab.country("icmm", "Country", "Turkey")["codes"] == ["TUR"]
    assert not vocab.country("usgs_global_ree", "Country", "Tibet")["codes"]


def test_list_membership_does_not_expand_group_names(vocab):
    entries = vocab.entries["commodities"]
    assert sum(entry["on_us_list"] for entry in entries.values()) == 60
    assert entries["Nd"]["on_us_list"] and entries["Sc"]["on_us_list"]
    assert not entries["REE"]["on_us_list"] and not entries["Pm"]["on_us_list"]
    assert all(entries[code]["excluded"] for code in ("MetCoal", "U", "Potash", "Phosphate"))


def test_reviewed_hs_expectation_survives_mapping_removal(vocab):
    row = {
        "kind": "commodity",
        "source_id": "oecd_export",
        "field": "COMMODITY",
        "raw_value": "HS07_260500",
    }
    assert expected_families(vocab, row) == {"Co"}


def test_committed_audit_recomputes_without_raw_downloads():
    receipt = check(ROOT)
    assert receipt["pilot_unmapped"] == 0
    assert receipt["normalized_rows"] == 0
    assert len(receipt["sources"]) == 16


@pytest.mark.parametrize(
    "source,field,raw",
    [
        ("usgs_mrds", "code", "CO2"),
        ("usgs_mrds", "code", "NA_SC"),
        ("jasansky", "material_id", "Me.Li2CO3"),
    ],
)
def test_formula_and_dictionary_tokens_are_not_substring_matches(vocab, source, field, raw):
    row = {"kind": "commodity", "source_id": source, "field": field, "raw_value": raw}
    assert expected_families(vocab, row) == set()
    assert not set(vocab.resolve("commodity", source, field, raw)["codes"]) & vocab.pilot_codes


def test_codebook_coverage_catches_missing_mapping(vocab):
    from copy import deepcopy

    from pipeline.vocab.audit import expectation_checks

    changed = deepcopy(vocab)
    del changed.rules["commodity_fields"]["oecd_export"]["COMMODITY"]["values"]["HS07_260500"]
    changed.aliases["commodities"].pop(("oecd_export", "hs07_260500"), None)
    failures = expectation_checks(changed)["failures"]
    assert any(case["raw_value"] == "HS07_260500" for case in failures)


def test_production_header_is_not_praseodymium(vocab):
    row = {
        "kind": "commodity",
        "source_id": "usgs_laterites",
        "field": "Ni pr grade%",
        "raw_value": "Ni pr grade%",
        "value_origin": "column_header",
    }
    assert expected_families(vocab, row) == {"Ni"}


def test_codebook_coverage_catches_extra_pilot_mapping(vocab):
    from copy import deepcopy

    from pipeline.vocab.audit import expectation_checks

    changed = deepcopy(vocab)
    changed.rules["commodity_fields"]["oecd_export"]["COMMODITY"]["values"]["HS07_260500"] = [
        "Co",
        "Ni",
    ]
    assert any(
        case["raw_value"] == "HS07_260500" for case in expectation_checks(changed)["failures"]
    )


def test_historical_bare_congo_requires_review(vocab):
    assert vocab.country("usgs_global_ree", "Country", "Congo")["codes"] == []
