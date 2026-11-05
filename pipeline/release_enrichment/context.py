"""Country observations with their original units, dates, scope and publication evidence."""

from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET
from pathlib import Path

from pipeline.normalize.core import sha256
from pipeline.release_enrichment.common import (
    PILOT,
    RELEASE,
    STAGES,
    load,
    pilot_rows,
    provenance,
    seal,
)
from pipeline.scaffold import json_bytes

STAGE = "context"
FOLDER = f"{RELEASE}/context"
CHAPTER_FILE = Path(__file__).with_name("mcs_chapters.json")
CHAPTERS = {"COBALT": "Co", "NICKEL": "Ni", "RARE EARTHS": "REE"}
LEADERS = {"Co": "COD", "Ni": "IDN", "REE": "CHN"}
SOURCE_FIELDS = [
    "record_id",
    "source_id",
    "source_table",
    "source_row",
    "source_file",
    "source_url",
    "raw_sha256",
    "source_currency",
    "retrieved_at",
    "evidence_grade",
    "raw_fields",
    "pilot_families",
    "country_iso3s",
]


def native_rows(root, source):
    registry = load(root / f"sources/{source}.yaml")
    for row in pilot_rows(root, source, SOURCE_FIELDS):
        for field in ("raw_fields", "country_iso3s", "pilot_families"):
            row[field] = json.loads(row[field])
        row["redistribution_ok"] = registry["redistribution_ok"]
        yield row


def quantity(raw):
    text = str(raw).replace(",", "").strip()
    if text in ("—", "–"):
        return 0, "zero"
    if re.fullmatch(r"\d+(?:\.\d+)?", text):
        return float(text), "reported"
    if re.fullmatch(r">\s*\d+(?:\.\d+)?", text):
        return None, "lower_bound"
    if text in {"NA", "N/A"}:
        return None, "not_available"
    if text == "W":
        return None, "withheld"
    raise ValueError(f"Unrecognized MCS quantity: {raw}")


def chapter_fact(chapter, row, field, raw, published, country, year):
    record = provenance(
        row,
        {
            k: row["raw_fields"][k]
            for k in (
                "MCS chapter",
                "Section",
                "Commodity",
                "Country",
                "Statistics",
                "Statistics_detail",
                "Unit",
                "Year",
                "Value",
                "Notes",
                "Other notes",
            )
        },
        "normalized_mcs_observation",
        "Original statistical row with its exact units and qualifiers.",
    )
    return {
        "source_id": "usgs_mcs_2026",
        "record_id": row["record_id"],
        "source_url": chapter["url"],
        "source_id_kind": "chapter_table_observation",
        "source_table": chapter["locator"],
        "source_row": country,
        "source_file": chapter["file"],
        "source_currency": "USGS MCS 2026",
        "raw_sha256": chapter["sha256"],
        "retrieved_at": chapter["retrieved_at"],
        "evidence_grade": chapter["evidence_grade"],
        "redistribution_ok": chapter["redistribution_ok"],
        "license": chapter["license"],
        "field": field,
        "year": year,
        "published_value": published,
        "staged_value": raw,
        "revised_since_staged_csv": str(raw).replace(",", "").strip() != published,
        "staged_observation": record,
    }


def country_commodities(source_rows, chapters):
    selected = {}
    for row in source_rows:
        raw = row["raw_fields"]
        family = CHAPTERS.get(raw["MCS chapter"])
        if not family or raw["Section"] != "World Mine Production and Reserves":
            continue
        detail = {"Production": "Mine production", "Reserves": "Reserves"}.get(raw["Statistics"])
        if not detail:
            continue
        if raw["Country"] == "World total":
            detail += ": rounded"
        if raw["Statistics_detail"] != detail:
            continue
        if raw["Unit"] != "metric tons":
            raise ValueError("Unexpected MCS statistical unit")
        key = family, raw["Country"], int(raw["Year"]), raw["Statistics"]
        if key in selected:
            raise ValueError("Duplicate MCS statistic")
        selected[key] = row
    rows = []
    seen = set()
    for family, chapter in chapters.items():
        by_country = {r[0]: r for r in chapter["rows"]}
        if len(by_country) != len(chapter["rows"]):
            raise ValueError("Duplicate chapter row")
        for name, table in by_country.items():
            for year, index in ((2024, 1), (2025, 2)):
                prod_key = (family, name, year, "Production")
                prod = selected[prod_key]
                seen.add(prod_key)
                iso = prod["country_iso3s"]
                scope = (
                    "world"
                    if name == "World total"
                    else "other_countries"
                    if name == "Other countries"
                    else "country"
                )
                if (scope == "country" and len(iso) != 1) or (scope != "country" and iso):
                    raise ValueError("Unresolved country or invalid MCS aggregate")
                pvalue, pqual = quantity(table[index])
                if pvalue is None:
                    raise ValueError("World-share accounting needs a numeric production value")
                result = {
                    "country_iso3": iso[0] if iso else None,
                    "country_name": name,
                    "scope": scope,
                    "commodity": family,
                    "year": year,
                    "production": pvalue,
                    "production_qualifier": pqual,
                    "production_estimated": year == 2025
                    or "estimated" in prod["raw_fields"]["Notes"].lower(),
                    "reserves": None,
                    "reserves_raw": None,
                    "reserves_qualifier": None,
                    "reserves_lower_bound": None,
                    "unit": "metric tons",
                    "quantity_basis": chapter["quantity_basis"],
                    "production_evidence": chapter_fact(
                        chapter,
                        prod,
                        "production",
                        prod["raw_fields"]["Value"],
                        table[index],
                        name,
                        year,
                    ),
                    "reserves_evidence": None,
                }
                if year == 2025:
                    rk = (family, name, year, "Reserves")
                    rr = selected[rk]
                    seen.add(rk)
                    value, qual = quantity(table[3])
                    result.update(
                        reserves=value,
                        reserves_raw=table[3],
                        reserves_qualifier=qual,
                        reserves_lower_bound=float(table[3][1:]) if qual == "lower_bound" else None,
                        reserves_evidence=chapter_fact(
                            chapter, rr, "reserves", rr["raw_fields"]["Value"], table[3], name, year
                        ),
                    )
                world = float(by_country["World total"][index])
                result["world_share_pct"] = round(100 * pvalue / world, 6)
                rows.append(result)
    if set(selected) != seen:
        raise ValueError("Chapter and normalized statistical rows disagree on coverage")
    minerals = []
    for family in sorted(chapters):
        summaries = []
        for year in (2024, 2025):
            period = [r for r in rows if r["commodity"] == family and r["year"] == year]
            world = next(r for r in period if r["scope"] == "world")
            other = next(r for r in period if r["scope"] == "other_countries")
            named = sorted(
                (r for r in period if r["scope"] == "country"),
                key=lambda r: (-r["production"], r["country_iso3"]),
            )
            total = sum(r["production"] for r in period if r["scope"] != "world")
            reported = world["production"]
            wtext = str(int(reported))
            half_rounding = 0.5 * 10 ** (len(wtext) - len(wtext.rstrip("0")))
            delta = total - reported
            if abs(delta) > half_rounding:
                raise ValueError("Shares exceed the published world total rounding allowance")
            if year == 2025 and named[0]["country_iso3"] != LEADERS[family]:
                raise ValueError("Producer leader conflicts with MCS Table 7")
            summaries.append(
                {
                    "year": year,
                    "world_production": reported,
                    "world_reserves": world["reserves"],
                    "world_reserves_raw": world["reserves_raw"],
                    "world_reserves_lower_bound": world["reserves_lower_bound"],
                    "world_production_evidence": world["production_evidence"],
                    "world_reserves_evidence": world["reserves_evidence"],
                    "reported_country_and_other_total": total,
                    "other_production": other["production"],
                    "other_share_pct": other["world_share_pct"],
                    "sum_of_reported_shares_pct": round(100 * total / reported, 6),
                    "rounding_residual_tonnes": delta,
                    "world_rounding_half_interval_tonnes": half_rounding,
                    "rounding_check_passed": True,
                    "producers": [
                        {
                            k: r[k]
                            for k in (
                                "country_iso3",
                                "country_name",
                                "production",
                                "world_share_pct",
                            )
                        }
                        for r in named
                        if r["production"] > 0
                    ],
                }
            )
        minerals.append(
            {
                "commodity": family,
                "year": 2025,
                "unit": "metric tons",
                "quantity_basis": chapters[family]["quantity_basis"],
                "years": summaries,
                "top_producer": summaries[-1]["producers"][0],
                "top_producer_check": "USGS MCS 2026 Table 7 and chapter table",
                "chapter_url": chapters[family]["url"],
                "summary_url": "https://pubs.usgs.gov/periodicals/mcs2026/mcs2026.pdf",
                "note": (
                    "Cobalt chapter prose estimates 73%; the rounded chapter quantities "
                    "yield 74.19%."
                )
                if family == "Co"
                else None,
            }
        )
    return sorted(rows, key=lambda r: (r["commodity"], r["year"], r["country_name"])), minerals


def codelists(root):
    directory = next((root / "data/raw/oecd_export").glob("????-??-??/manifest.json")).parent
    manifest = load(directory / "manifest.json")
    details = next(x for x in manifest["files"] if x["path"].endswith(".xml"))
    path = directory / details["path"]
    if sha256(path) != details["sha256"]:
        raise ValueError("OECD structure has changed")
    tree = ET.parse(path)
    result = {}
    for item in tree.findall(".//{*}Codelist"):
        result[item.attrib["id"]] = {
            code.attrib["id"]: next(
                (
                    n.text
                    for n in code.findall("{*}Name")
                    if n.get("{http://www.w3.org/XML/1998/namespace}lang") == "en"
                ),
                None,
            )
            for code in item.findall("{*}Code")
        }
    return result, details["sha256"]


def restriction_rows(rows, labels):
    groups = {}
    for row in rows:
        f = row["raw_fields"]
        key = tuple(
            f[k]
            for k in ("REF_AREA", "COMMODITY", "POLICY_TYPE", "POLICY_VARIATION", "TIME_PERIOD")
        )
        country, hs, policy, variant, year = key
        if not (2009 <= int(year) <= 2024):
            raise ValueError("OECD baseline year outside accepted coverage")
        desc = f["POLICY_DESCRIPTION"]
        if key not in groups:
            iso = row["country_iso3s"]
            group = {
                "country_iso3": iso[0] if len(iso) == 1 else None,
                "source_area": country,
                "commodity_families": row["pilot_families"],
                "product_code": hs,
                "product": labels["CL_PRODUCT_HS2007"][hs],
                "measure_code": policy,
                "measure_type": labels["CL_POLTYPE_EXP_RESTRIC"][policy],
                "variation": variant,
                "year": int(year),
                "as_of_year": int(year),
                "scope": "historical_inventory_observation",
                "start_date_raw": None,
                "end_date_raw": None,
                "descriptions": {},
                "source_evidence": provenance(
                    row,
                    {
                        "REF_AREA": country,
                        "COMMODITY": hs,
                        "POLICY_TYPE": policy,
                        "POLICY_VARIATION": variant,
                        "TIME_PERIOD": year,
                    },
                    "oecd_inventory",
                    (
                        "Annual OECD inventory observation. The description fields concern "
                        "the same measure and product."
                    ),
                ),
            }
            groups[key] = group
        group = groups[key]
        if desc in group["descriptions"]:
            raise ValueError("Duplicate OECD description dimension")
        group["descriptions"][desc] = {
            "field": labels["CL_POLDESCRIP_EXP_RESTRIC"][desc],
            "value": f["OBS_VALUE"],
            "record_id": row["record_id"],
            "source_row": row["source_row"],
        }
        if desc in {"DATI", "END"}:
            group["start_date_raw" if desc == "DATI" else "end_date_raw"] = f["OBS_VALUE"]
    return [groups[k] for k in sorted(groups)]


def finance_rows(rows):
    result = []
    seen = set()
    for row in rows:
        if not row["source_table"].endswith("!Financial_Contribution"):
            continue
        f = row["raw_fields"]
        identifier = str(f["AidData_Record_ID"])
        if identifier in seen:
            raise ValueError("Duplicate AidData financial contribution")
        seen.add(identifier)
        host = f.get("Country_of_Activity_ISO3")
        if host and (len(row["country_iso3s"]) != 1 or row["country_iso3s"][0] != host):
            raise ValueError("AidData contribution host country conflict")
        result.append(
            {
                "aiddata_record_id": identifier,
                "country_iso3": host,
                "country_name": f.get("Country_of_Activity"),
                "commodity_families": row["pilot_families"],
                "title": f.get("Title"),
                "year": f.get("Commitment_Year"),
                "commitment_date": f.get("Commitment_Date"),
                "commitment_date_estimated": f.get("Commitment_Date_Estimated"),
                "lender": f.get("Funding_Agencies"),
                "flow_type": f.get("Flow_Type"),
                "source_status": f.get("Status"),
                "amount_constant_usd_2023": f.get("Amount_Constant_USD_2023"),
                "amount_nominal_usd": f.get("Amount_Nominal_USD"),
                "amount_original_currency": f.get("Amount_Original_Currency"),
                "original_currency": f.get("Original_Currency"),
                "amount_estimated": f.get("Amount_Estimated"),
                "recommended_for_aggregates": f.get("Recommended_for_Aggregates"),
                "source_mining_site": f.get("Mining_Site"),
                "source_mining_site_id": f.get("Mining_Site_ID"),
                "site_id": None,
                "site_link_reason": (
                    "The contribution provides no verified site coordinate and Atlas "
                    "identifier crosswalk."
                ),
                "source_evidence": provenance(
                    row,
                    {
                        "AidData_Record_ID": identifier,
                        "Commitment_Year": f.get("Commitment_Year"),
                        "Country_of_Activity_ISO3": host,
                    },
                    "aiddata_contribution",
                    (
                        "Financial contribution from the named workbook sheet. Related loan "
                        "events and organization roles are distinct entities."
                    ),
                ),
            }
        )
    return sorted(result, key=lambda r: r["aiddata_record_id"])


def build(root: Path, docs: Path):
    previous = load(root / STAGES["maturity"][0])
    if previous.get("status") != "accepted" or previous.get("spot_check_passed") is not True:
        raise ValueError("Accepted Step 7 maturity spot check required")
    chapters = load(CHAPTER_FILE)
    files = {}
    for chapter in chapters.values():
        path = docs / chapter["file"]
        if sha256(path) != chapter["sha256"] or path.stat().st_size != chapter["bytes"]:
            raise ValueError("Verified chapter PDF changed")
        files[f"{FOLDER}/primary/{chapter['file']}"] = path.read_bytes()
    country, minerals = country_commodities(native_rows(root, "usgs_mcs_2026"), chapters)
    labels, structure_hash = codelists(root)
    restrictions = restriction_rows(native_rows(root, "oecd_export"), labels)
    finance = finance_rows(native_rows(root, "aiddata_transition_v2"))
    groups = {
        "country_commodity": country,
        "minerals": minerals,
        "export_restrictions": restrictions,
        "china_official_finance": finance,
    }
    files.update({f"{FOLDER}/{name}.json": json_bytes(value) for name, value in groups.items()})
    revisions = [
        {
            "country_name": r["country_name"],
            "commodity": r["commodity"],
            "year": r["year"],
            "field": k,
            "staged": r[k + "_evidence"]["staged_value"],
            "chapter": r[k + "_evidence"]["published_value"],
        }
        for r in country
        for k in ("production", "reserves")
        if r[k + "_evidence"] and r[k + "_evidence"]["revised_since_staged_csv"]
    ]
    files[f"{FOLDER}/mcs-revisions.json"] = json_bytes(revisions)
    receipt = {
        "schema_version": "release-country-context-v1",
        "step": 8,
        "status": "accepted",
        "parent_receipt_sha256": sha256(root / STAGES["maturity"][0]),
        "pilot_receipt_sha256": sha256(root / PILOT),
        "oecd_structure_sha256": structure_hash,
        "chapter_sha256": {k: v["sha256"] for k, v in chapters.items()},
        "counts": {k: len(v) for k, v in groups.items()},
        "mcs_revised_observations": len(revisions),
        "rounded_share_checks_passed": sum(
            y["rounding_check_passed"] for m in minerals for y in m["years"]
        ),
        "top_producers_verified": {
            m["commodity"]: m["top_producer"]["country_iso3"] for m in minerals
        },
        "finance_site_links": 0,
        "oecd_coverage": [2009, 2024],
    }
    summary = "\n".join(
        (
            f"| {m['commodity']} | {m['top_producer']['country_name']} | "
            f"{m['top_producer']['world_share_pct']:.2f}% | "
            f"{m['years'][-1]['sum_of_reported_shares_pct']:.2f}% |"
        )
        for m in minerals
    )
    report = f"""# Phase 0 Step 8. Country context

The 2025 mine-production leaders agree with the USGS chapter tables and Table 7.
Each share uses the published rounded world total. The summed contributions include named
countries and the original Other category.
All six commodity/year sums fall inside the precision of the printed world total.

| Mineral | Leading producer | World share | Sum of country and Other shares |
| --- | --- | ---: | ---: |
{summary}

Rare-earth quantities are metric tons of rare-earth-oxide equivalent. Nickel and cobalt
quantities are metric tons of metal content.
The original statistical export and independently retrieved chapter tables retain separate
provenance. {len(revisions)} changed values or qualifiers are recorded in the private revision
table.

The OECD inventory supplies {len(restrictions):,} dated country/product/measure/year
observations for 2009–2024.
AidData supplies {len(finance):,} financial contributions. Loan-event and organization-role
sheets are excluded from contributions.
Country-level records retain their dates and lender fields. Site links require an independently
verified asset match.
"""
    files[STAGES[STAGE][1]] = report.encode()
    return files, receipt


def prepare(root, docs):
    files, receipt = build(root, docs)
    return seal(root, STAGE, files, receipt)
