"""Country roles and code systems must not silently change record geography."""

import json
from pathlib import Path

import pytest

from pipeline.pilot.countries import country_evidence
from pipeline.vocab.core import Vocabulary


@pytest.fixture(scope="module")
def vocab():
    return Vocabulary(Path(__file__).resolve().parents[1])


def row(normalized_country=None, **fields):
    return {"country": normalized_country, "raw_fields": json.dumps(fields)}


def test_finance_counterparties_never_assign_host_country(vocab):
    value = row(
        "Zambia",
        Country_of_Activity="Zambia",
        Country_of_Activity_ISO3="ZMB",
        Financier_Country="China (People's Republic of)",
        DRA_Country_of_Inc="Hong Kong (China)",
        DRA_Country_of_Inc_ISO3="HKG",
        Overseas_Jurisdiction="Switzerland|France|United Kingdom",
        Overseas_Jurisdiction_ISO3="CHE|FRA|GBR",
    )
    result = country_evidence("aiddata_transition_v2", value, vocab)
    assert result["codes"] == ["ZMB"]
    assert not result["unresolved"]
    overseas = next(e for e in result["evidence"] if e["field"] == "Overseas_Jurisdiction_ISO3")
    assert overseas["codes"] == ["CHE", "FRA", "GBR"]
    assert not overseas["assigns_country"]
    assert overseas["role"] == "overseas_lender_jurisdiction"


def test_explicit_finance_host_list_is_multi_country(vocab):
    result = country_evidence(
        "aiddata_transition_v2",
        row(
            "Zambia|Zimbabwe",
            Country_of_Activity="Zambia|Zimbabwe",
            Country_of_Activity_ISO3="ZMB|ZWE",
        ),
        vocab,
    )
    assert result["codes"] == ["ZMB", "ZWE"]
    assert not result["unresolved"]


def test_host_absence_or_region_is_not_replaced_by_financier(vocab):
    absent = country_evidence(
        "aiddata_transition_v2",
        row(Financier_Country="China"),
        vocab,
    )
    assert absent["codes"] == []
    assert absent["unresolved"] == ["Country not supplied"]
    region = country_evidence(
        "aiddata_transition_v2",
        row("Africa, regional", Country_of_Activity="Africa, regional", Financier_Country="China"),
        vocab,
    )
    assert region["codes"] == []
    assert region["unresolved"] == ["Africa, regional"]


def test_owner_country_list_is_evidence_only(vocab):
    result = country_evidence(
        "aiddata_transition_v2",
        row("Indonesia", Host_Country="Indonesia", Equity_Holder_Origin="China|Indonesia"),
        vocab,
    )
    assert result["codes"] == ["IDN"]
    owner = next(e for e in result["evidence"] if e["field"] == "Equity_Holder_Origin")
    assert owner["codes"] == ["CHN", "IDN"]
    assert not owner["assigns_country"]


@pytest.mark.parametrize(
    "source,fields",
    [
        ("gseu_crm", {"Country": "Kosovo", "ISO3": "XKX"}),
        ("usgs_global_critical", {"LOCATION": "Kosovo"}),
        ("usgs_laterites", {"Country": "Kosovo (Serbia)", "CountryCode": "KV"}),
    ],
)
def test_kosovo_never_becomes_serbia_or_fabricated_iso(source, fields, vocab):
    result = country_evidence(source, row(**fields), vocab)
    assert result["codes"] == []
    assert result["unresolved"]


def test_iso_preferred_but_conflicting_valid_names_are_not_discarded(vocab):
    consistent = country_evidence(
        "gseu_crm",
        row("Macedonia", ISO3="MKD", Country="Macedonia"),
        vocab,
    )
    assert consistent["codes"] == ["MKD"]
    assert next(e for e in consistent["evidence"] if e["field"] == "ISO3")["preferred"]
    conflict = country_evidence(
        "jasansky",
        row("Australia", GID_0="CAN", country="Australia"),
        vocab,
    )
    assert conflict["codes"] == []
    assert set(conflict["unresolved"]) == {"CAN", "Australia"}
    assert all(e.get("conflict") for e in conflict["evidence"])


def test_invalid_explicit_iso_cannot_fall_back_to_other_geography(vocab):
    result = country_evidence("gseu_crm", row("Serbia", ISO3="XKX", Country="Serbia"), vocab)
    assert result["codes"] == []
    assert "XKX" in result["unresolved"]
    unknown_spelling = country_evidence(
        "gseu_crm",
        row("unreviewed name", ISO3="AUT", Country="unreviewed name"),
        vocab,
    )
    assert unknown_spelling["codes"] == ["AUT"]
    assert unknown_spelling["unresolved"] == ["unreviewed name"]


def test_mrds_fips_and_laterite_codes_are_not_iso(vocab):
    value = row(
        linked_tables={
            "Place.txt": [
                {"row_number": 7, "values": {"fips": "AS"}},
                {"row_number": 8, "values": {"fips": "fAF"}},
            ]
        }
    )
    result = country_evidence("usgs_mrds", value, vocab)
    assert result["codes"] == []
    assert set(result["unresolved"]) == {"AS", "fAF"}
    assert all("FIPS" in e["basis"] for e in result["evidence"])
    laterite = country_evidence(
        "usgs_laterites",
        row("Australia", Country="Australia", CountryCode="AS"),
        vocab,
    )
    assert laterite["codes"] == ["AUS"]
    assert "ASM" not in laterite["codes"]


@pytest.mark.parametrize(
    "state,expected",
    [
        ("AZ", ["USA"]),
        ("PR", ["PRI"]),
        ("Puerto Rico", ["PRI"]),
        ("UT\nNV", ["USA"]),
        ("PR/AZ", []),
        ("GU", []),
        ("unknown", []),
    ],
)
def test_us_state_jurisdiction_is_exact_and_preserves_puerto_rico(state, expected, vocab):
    value = row(state=state)
    value.update(lat=18.4, lon=-66.1)
    result = country_evidence("usgs_us_critical", value, vocab)
    assert result["codes"] == expected
    assert result["evidence"][0]["raw_value"] == state
    assert "native state" in result["evidence"][0]["basis"]


@pytest.mark.parametrize(
    "source,field",
    [
        ("ga_critical_2025", "State"),
        ("ga_critical_2025_arcgis", "state"),
    ],
)
def test_australian_state_scope_does_not_confuse_wa_with_washington(source, field, vocab):
    assert country_evidence(source, row(**{field: "WA"}), vocab)["codes"] == ["AUS"]
    assert country_evidence(source, row(**{field: "PR"}), vocab)["codes"] == []


def test_no_generic_splitting_or_coordinate_lookup(vocab):
    aggregate = country_evidence(
        "usgs_mcs_2026",
        row("China, Germany, and Russia", Country="China, Germany, and Russia"),
        vocab,
    )
    assert aggregate["codes"] == []
    pipe = country_evidence("icmm", row("China|Indonesia", Country="China|Indonesia"), vocab)
    assert pipe["codes"] == []
    wapha = country_evidence("wapha", {**row(), "lat": 30.0, "lon": 110.0}, vocab)
    assert wapha["codes"] == []
    assert wapha["unresolved"] == ["Country not supplied"]


def test_raw_fields_input_validation_and_no_mutation(vocab):
    original = {"country": "Zambia", "raw_fields": {"Country": "Zambia"}}
    saved = json.dumps(original)
    country_evidence("usgs_mcs_2026", original, vocab)
    assert json.dumps(original) == saved
    with pytest.raises(ValueError, match="JSON object"):
        country_evidence("icmm", {"raw_fields": "[]"}, vocab)
