import copy

import pytest

from pipeline.release_enrichment import context
from pipeline.release_enrichment.common import load


def member(source, raw, name="Test deposit", **extra):
    return {
        "record_id": f"{source}:test",
        "source_id": source,
        "raw_name": name,
        "raw_fields": raw,
        "source_table": "table",
        "source_row": 12,
        "source_file": "immutable.csv",
        "source_url": "https://example.org/data",
        "raw_sha256": "a" * 64,
        "source_currency": "as published",
        "retrieved_at": None,
        "evidence_grade": "B",
        "redistribution_ok": True,
        "raw_commodities": {"commodity": "Ni"},
        "raw_status": {},
        "raw_size_grade_text": {},
        "record_kind": "deposit",
        "country": None,
        **extra,
    }


def mcs_rows(chapters):
    aliases = {"COBALT": "Co", "NICKEL": "Ni", "RARE EARTHS": "REE"}
    result = []
    for chapter_name, fam in aliases.items():
        for c in chapters[fam]["rows"]:
            for stat, year, index in [
                ("Production", 2024, 1),
                ("Production", 2025, 2),
                ("Reserves", 2025, 3),
            ]:
                name = c[0]
                iso = {"Congo (Kinshasa)": "COD", "China": "CHN", "Indonesia": "IDN"}.get(
                    name, name[:3].upper()
                )
                row = member(
                    "usgs_mcs_2026",
                    {
                        "MCS chapter": chapter_name,
                        "Section": "World Mine Production and Reserves",
                        "Commodity": fam,
                        "Country": name,
                        "Statistics": stat,
                        "Statistics_detail": "Mine production"
                        if stat == "Production"
                        else "Reserves",
                        "Unit": "metric tons",
                        "Year": str(year),
                        "Value": c[index],
                        "Notes": "Estimated.",
                        "Other notes": "",
                    },
                )
                if name == "World total":
                    row["raw_fields"]["Statistics_detail"] += ": rounded"
                row["country_iso3s"] = [] if name in ("World total", "Other countries") else [iso]
                result.append(row)
    return result


def test_country_shares_keep_original_rounding_other_qualifiers_and_historical_csv():
    chapters = load(context.CHAPTER_FILE)
    source = mcs_rows(chapters)
    change = next(
        r
        for r in source
        if r["raw_fields"]["MCS chapter"] == "RARE EARTHS"
        and r["raw_fields"]["Country"] == "Brazil"
        and r["raw_fields"]["Statistics"] == "Reserves"
    )
    change["raw_fields"]["Value"] = "21,000,000"
    rows, minerals = context.country_commodities(source, chapters)
    assert len(rows) == 86
    cobalt = next(m for m in minerals if m["commodity"] == "Co")
    assert cobalt["top_producer"]["country_iso3"] == "COD"
    assert cobalt["years"][-1]["sum_of_reported_shares_pct"] == 101.483871
    assert cobalt["years"][-1]["other_production"] == 9100
    brazil = next(
        r
        for r in rows
        if r["commodity"] == "REE" and r["country_name"] == "Brazil" and r["year"] == 2025
    )
    assert (
        brazil["reserves"] == 11000000 and brazil["reserves_evidence"]["revised_since_staged_csv"]
    )
    assert brazil["reserves_evidence"]["staged_observation"]["retrieved_at"] is None
    assert brazil["reserves_evidence"]["retrieved_at"] == chapters["REE"]["retrieved_at"]
    world = next(
        r for r in rows if r["commodity"] == "REE" and r["scope"] == "world" and r["year"] == 2025
    )
    assert world["reserves"] is None and world["reserves_lower_bound"] == 75000000
    canada = next(r for r in rows if r["commodity"] == "REE" and r["country_name"] == "Canada")
    assert canada["production"] == 0 and canada["production_qualifier"] == "zero"
    assert "oxide equivalent" in world["quantity_basis"]


def test_inconsistent_chapter_total_is_rejected():
    chapters = load(context.CHAPTER_FILE)
    source = mcs_rows(chapters)
    chapters["Ni"]["rows"][-1][2] = "3000000"
    with pytest.raises(ValueError, match="rounding allowance"):
        context.country_commodities(source, chapters)


def test_oecd_groups_descriptions_instead_of_counting_them_as_separate_measures():
    code = {
        "REF_AREA": "IDN",
        "COMMODITY": "HS07_1",
        "POLICY_TYPE": "M4",
        "POLICY_VARIATION": "A1",
        "TIME_PERIOD": "2024",
    }
    labels = {
        "CL_PRODUCT_HS2007": {"HS07_1": "Nickel product"},
        "CL_POLTYPE_EXP_RESTRIC": {"M4": "Export prohibition"},
        "CL_POLDESCRIP_EXP_RESTRIC": {"END": "Ending date", "TEMP": "Temporary?"},
    }
    rows = [
        member(
            "oecd_export",
            {**code, "POLICY_DESCRIPTION": k, "OBS_VALUE": v},
            country_iso3s=["IDN"],
            pilot_families=["Ni"],
        )
        for k, v in [("END", "nd"), ("TEMP", "no")]
    ]
    out = context.restriction_rows(rows, labels)
    assert len(out) == 1 and out[0]["scope"] == "historical_inventory_observation"
    assert out[0]["end_date_raw"] == "nd" and "active" not in out[0]
    with pytest.raises(ValueError, match="Duplicate"):
        context.restriction_rows(rows + rows, labels)


def test_aiddata_contribution_keeps_amount_basis_and_never_links_from_a_name():
    raw = {
        "AidData_Record_ID": 12,
        "Country_of_Activity_ISO3": "IDN",
        "Country_of_Activity": "Indonesia",
        "Commitment_Year": 2020,
        "Funding_Agencies": "Reported lender",
        "Mining_Site": "Similar site",
        "Amount_Constant_USD_2023": 42,
        "Amount_Nominal_USD": 40,
    }
    row = member(
        "aiddata_transition_v2",
        raw,
        source_table="source.xlsx!Financial_Contribution",
        country_iso3s=["IDN"],
        pilot_families=["Ni"],
    )
    role = copy.deepcopy(row)
    role["source_table"] = "source.xlsx!Org_Role"
    out = context.finance_rows([row, role])
    assert len(out) == 1 and out[0]["site_id"] is None
    assert out[0]["amount_constant_usd_2023"] == 42 and out[0]["amount_nominal_usd"] == 40
    with pytest.raises(ValueError, match="Duplicate"):
        context.finance_rows([row, row])
