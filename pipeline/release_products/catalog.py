"""Prepare the private Atlas catalog and the independently filtered download tables."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from math import ceil
from zoneinfo import ZoneInfo

import pycountry
import yaml

from .common import BASE, CONTEXT, QA, TAG, bound, load, sha
from .production_evidence import reviewed_for_release
from .quality import FILE, evidence_holds, inputs, source_registry, stage

NAMES = {"REE": "Rare earth elements", "Ni": "Nickel", "Co": "Cobalt"}
COUNTRY_LABELS = {
    "COD": "Democratic Republic of the Congo",
    "COG": "Republic of the Congo",
    "KOR": "South Korea",
    "PRK": "North Korea",
    "LAO": "Laos",
    "VNM": "Vietnam",
    "RUS": "Russia",
    "IRN": "Iran",
    "TZA": "Tanzania",
    "BOL": "Bolivia",
    "VEN": "Venezuela",
    "SYR": "Syria",
}


def facts(ev):
    return {
        k: ev.get(k)
        for k in ("source_id", "source_url", "retrieved_at", "evidence_grade", "raw_sha256")
    }


def citing(ev, field):
    return {
        **facts(ev),
        "field": field,
        "record_id": ev.get("record_id"),
        "source_row": ev.get("source_row"),
        "source_table": ev.get("source_table"),
        "native_id": ev.get("native_row_id"),
        "as_of": str(ev.get("year") or ev.get("source_currency") or "") or None,
    }


def ui_evidence(ev, note=None):
    explanation = note or ev.get("reason") or ""
    if ev.get("native_row_id") and ev.get("source_table") == "data/reserves.csv":
        f = ev["fields"]
        ref = (ev.get("original_citation") or {}).get("sources", "")
        explanation = (
            f"{ev['year']} estimate: {int(float(f['mineral_value_tonnes'])):,} tonnes "
            f"at {float(f['grade_ppm']):,.0f} ppm {f['commodity'].removeprefix('Me.')}. "
            f"Reserve table row {ev['native_row_id']}. {ref}"
        )
    return {
        "source_id": ev["source_id"],
        "record_id": ev.get("record_id"),
        "url": ev["source_url"],
        "grade": ev.get("evidence_grade"),
        "date": str(ev.get("year") or ev.get("source_currency") or "") or None,
        "note": explanation,
    }


def ops(root, registry):
    manifest = load(root, "overrides/pilot_operator_evidence.json")
    raw_catalog = (root / manifest["path"]).read_bytes()
    if sha(raw_catalog) != manifest["sha256"]:
        raise ValueError("Changed operator evidence catalog")
    entries = yaml.safe_load(raw_catalog)
    docs = load(root, "overrides/pilot_operator_documents.json")
    out = {}
    for item in entries:
        sid = item["source_id"]
        d = docs[sid]
        raw = (root / BASE / "evidence/operators" / f"{sid}.html").read_bytes()
        if sha(raw) != d["sha256"] or len(raw) != d["bytes"] or d["url"] != item["url"]:
            raise ValueError("Operator citation differs from its archived source")
        registry[sid] = {
            "id": sid,
            "title": item["title"],
            "url": d["url"],
            "grade": item["evidence_grade"],
            "retrieved_at": d["retrieved_at"],
            "currency": item["reporting_period"],
            "license": "Issuer document. Factual citation in the private Atlas.",
            "attribution": item["title"],
            "redistribution_ok": False,
        }
        for rid in item["record_ids"]:
            if rid in out:
                raise ValueError("Competing operator statements require adjudication")
            out[rid] = (
                item,
                {
                    "source_id": sid,
                    "record_id": rid,
                    "url": d["url"],
                    "grade": item["evidence_grade"],
                    "date": item["reporting_period"]
                    or "Accessed "
                    + datetime.fromisoformat(d["retrieved_at"])
                    .astimezone(ZoneInfo("America/Los_Angeles"))
                    .date()
                    .isoformat(),
                    "note": item["claim"],
                },
            )
    return out


def verified_citation(ev, registry):
    doc = ev.get("verified_document")
    if not doc:
        return None
    identifier = doc.get("source_id")
    key = (
        identifier
        if identifier in registry and registry[identifier]["url"] == doc["url"]
        else "document_" + sha(doc["url"].encode())[:16]
    )
    registry[key] = {
        **registry.get(key, {}),
        "id": key,
        "title": doc.get("title") or "Supporting document",
        "url": doc["url"],
        "retrieved_at": doc.get("retrieved_at"),
        "grade": doc["evidence_grade"],
        "license": doc.get("license") or "Factual citation to original document",
        "redistribution_ok": doc.get("redistribution_ok") is True,
        "attribution": doc.get("title"),
        "checksum": doc.get("sha256"),
    }
    return {
        "source_id": key,
        "record_id": ev["record_id"],
        "url": doc["url"],
        "grade": doc["evidence_grade"],
        "date": doc.get("verified_as_of")
        or doc.get("statement_date")
        or ("2026" if "Mineral Commodity Summaries 2026" in doc.get("title", "") else None),
        "note": " ".join(
            x for x in [ev.get("reason"), doc.get("locator"), doc.get("effective_date_note")] if x
        ),
    }


def short(value):
    return f"{value:,.0f}" if isinstance(value, (int, float)) else value


def chapter_evidence(ev, registry, family):
    if not ev:
        return ev
    sid = "document_" + sha(ev["source_url"].encode())[:16]
    registry[sid] = {
        "id": sid,
        "title": f"USGS Mineral Commodity Summaries 2026 · {NAMES[family]}",
        "publisher": "U.S. Geological Survey",
        "url": ev["source_url"],
        "license": ev["license"],
        "redistribution_ok": ev["redistribution_ok"] is True,
        "grade": ev["evidence_grade"],
        "retrieved_at": ev["retrieved_at"],
        "currency": "2026",
        "checksum": ev["raw_sha256"],
        "attribution": "U.S. Geological Survey, Mineral Commodity Summaries 2026, "
        + NAMES[family]
        + " chapter.",
    }
    return {**ev, "source_id": sid}


def reserve_label(row):
    if row["reserves"] is not None:
        return short(row["reserves"])
    if row.get("reserves_lower_bound") is not None:
        return "More than " + short(row["reserves_lower_bound"])
    return {"withheld": "Withheld by publisher", "not_available": "Unavailable"}.get(
        row.get("reserves_qualifier"), "Unavailable"
    )


def assemble(root, *, with_audit=False):
    sites, members, observations, maturity = inputs(root)
    maturity, production_review = reviewed_for_release(root, sites, maturity)
    qa = {r["site_id"]: r for r in bound(root, QA, FILE)}
    reg = source_registry(root)
    operators = ops(root, reg)
    obs = {r["record_id"]: r for r in observations}
    grades = {r["site_id"]: r for r in maturity}
    visible = []
    public_record_ids = set()
    countries = {}
    tables = {
        k: []
        for k in (
            "source",
            "source_record",
            "site",
            "site_member",
            "facility",
            "production",
            "country_commodity",
            "export_restriction",
            "china_official_finance",
        )
    }

    def country(code):
        if code not in countries:
            c = pycountry.countries.get(alpha_3=code)
            if c is None:
                raise ValueError(f"Unknown country code {code}")
            countries[code] = {
                "code": code,
                "name": COUNTRY_LABELS.get(code, getattr(c, "common_name", c.name)),
                "context": [],
            }
        return countries[code]

    minerals = {k: {"code": k, "name": name, "context": []} for k, name in NAMES.items()}
    for site in sites:
        sid = site["site_id"]
        disposition = qa[sid]
        if not disposition["geographic_pass"]:
            continue
        mat = grades[sid]
        ids = site["record_ids"]
        anchor = obs[site["coordinate_record_id"]]
        base = {
            **anchor["evidence"],
            "record_id": anchor["record_id"],
            "source_id": anchor["source_id"],
            "source_url": anchor["source_url"],
        }
        evs = [ui_evidence(base, "Representative coordinate and site identity.")]
        detailed = [citing(base, "coordinate")]
        for field, ev in [
            ("maturity", mat["rung_evidence"]),
            ("status", mat.get("status_evidence")),
            *[("reporting_system", e) for e in mat.get("reporting_evidence", [])],
        ]:
            if not ev:
                continue
            evs.append(ui_evidence(ev))
            detailed.append(citing(ev, field))
            doc = verified_citation(ev, reg)
            if doc:
                evs.append(doc)
                d = ev["verified_document"]
                detailed.append(
                    {
                        "field": field,
                        "record_id": ev["record_id"],
                        "source_url": d["url"],
                        "retrieved_at": d["retrieved_at"],
                        "evidence_grade": d["evidence_grade"],
                        "raw_sha256": d["sha256"],
                        "source_id": doc["source_id"],
                    }
                )
        for rid in ids:
            r = obs[rid]
            if rid != anchor["record_id"]:
                ref = {
                    **r["evidence"],
                    "source_id": r["source_id"],
                    "source_url": r["source_url"],
                    "record_id": rid,
                }
                evs.append(ui_evidence(ref, "Reviewed member observation."))
                detailed.append(citing(ref, "site membership"))
        op = next((operators[rid] for rid in ids if rid in operators), None)
        if op:
            evs.append(op[1])
        code = site["country_iso3"]
        country(code)
        row = {
            "id": sid,
            "name": site["name"],
            "country_iso3s": [code],
            "minerals": site["commodities"],
            "lat": site["lat"],
            "lon": site["lon"],
            "location_precision": site["location_precision"],
            "entity_level": "site",
            "kind": "mine" if mat["maturity_rung"] == 5 else "deposit",
            "maturity_rung": mat["maturity_rung"],
            "maturity_reason": mat["rung_evidence"]["reason"],
            "status": mat["status"],
            "status_as_of": mat["status_as_of"],
            "reporting_system": mat["reporting_system"],
            "national_system": mat["national_system"],
            "source_ids": sorted({obs[r]["source_id"] for r in ids}),
            "aliases": sorted(
                {n for r in ids for n in obs[r]["aliases"] if n and n != site["name"]}
            ),
            "deposit_type": ", ".join(sorted({k for r in ids for k in obs[r]["deposit_types"]}))
            or None,
            "evidence": evs,
            "record_ids": ids,
            "review_state": site["review_state"],
            "operator": op[0]["operator"] if op else None,
            "operator_evidence": op[1] if op else None,
            "production_families": op[0]["production_families"] if op else [],
            "has_unreviewed_candidates": site.get("has_unreviewed_candidates"),
            "publisher_view_counterparts": site["publisher_view_counterparts"],
            "coincident_named_entries": disposition["coincident_named_entries"],
        }
        visible.append(row)
        if not disposition["distributable"]:
            continue
        if evidence_holds(mat["rung_evidence"], reg) or (
            mat["status_evidence"] and evidence_holds(mat["status_evidence"], reg)
        ):
            raise ValueError("New source evidence requires a distribution hold")
        public_record_ids.update(ids)
        exported = {
            k: site[k]
            for k in (
                "site_id",
                "name",
                "lat",
                "lon",
                "coordinate_record_id",
                "location_precision",
                "country_iso3",
                "commodities",
                "review_state",
                "record_ids",
            )
        }
        exported.update(
            {
                k: row[k]
                for k in (
                    "deposit_type",
                    "maturity_rung",
                    "maturity_reason",
                    "status",
                    "status_as_of",
                    "reporting_system",
                )
            }
        )
        exported.update(facts(base), evidence=detailed)
        tables["site"].append(exported)
        if row["kind"] == "mine":
            tables["facility"].append(
                {
                    "facility_id": "mine_" + sid,
                    "site_id": sid,
                    "name": row["name"],
                    "facility_type": "mine",
                    "lat": site["lat"],
                    "lon": site["lon"],
                    "commodities": site["commodities"],
                    "status": row["status"],
                    "status_as_of": row["status_as_of"],
                    "operator_name": None,
                    **facts(mat["rung_evidence"]),
                    "evidence": detailed,
                }
            )
    for member in members:
        rid = member["record_id"]
        if rid not in public_record_ids:
            continue
        r = obs[rid]
        base = {
            **r["evidence"],
            "source_id": r["source_id"],
            "source_url": r["source_url"],
            "record_id": rid,
        }
        tables["site_member"].append(
            {
                **{k: member[k] for k in ("site_id", "record_id", "method")},
                **facts(base),
                "evidence": [citing(base, "membership")],
            }
        )
        tables["source_record"].append(
            {
                "record_id": rid,
                "name": r["name"],
                "lat": r["lat"],
                "lon": r["lon"],
                "aliases": r["aliases"],
                "country_iso3": r["country"] or None,
                "commodities": r["minerals"],
                "deposit_types": r["deposit_types"],
                "raw_status": r["status"],
                "location_precision": r["location_precision"],
                **facts(base),
                "evidence": [citing(base, "source observation")],
            }
        )
    country_rows = stage(root, CONTEXT, "context", "country_commodity")
    for row in country_rows:
        for field in ("production_evidence", "reserves_evidence"):
            row[field] = chapter_evidence(row[field], reg, row["commodity"])

    def fact(label, value, unit, year, ev, note=None):
        return {
            "label": label,
            "value": value,
            "unit": unit,
            "year": year,
            "source_id": ev["source_id"],
            "url": ev["source_url"],
            "grade": ev["evidence_grade"],
            "note": note,
        }

    for r in country_rows:
        e = r["production_evidence"]
        reserve = r.get("reserves_evidence")
        f = r["commodity"]
        yr = r["year"]
        basis = {
            "Co": "tonnes of cobalt content",
            "Ni": "tonnes of nickel content",
            "REE": "tonnes REO equivalent",
        }[f]
        if yr == 2025 and r["scope"] in ("world", "country"):
            profile = minerals[f] if r["scope"] == "world" else country(r["country_iso3"])
            prefix = "World" if r["scope"] == "world" else NAMES[f]
            profile["context"].append(
                fact(
                    prefix + " mine production",
                    short(r["production"]),
                    basis,
                    yr,
                    e,
                    "USGS estimate" if r["production_estimated"] else None,
                )
            )
            if reserve:
                profile["context"].append(
                    fact(
                        prefix + " reserves",
                        reserve_label(r),
                        basis
                        if r["reserves"] is not None or r.get("reserves_lower_bound") is not None
                        else "",
                        yr,
                        reserve,
                    )
                )
            if r["scope"] == "country":
                profile["context"].append(
                    fact(
                        NAMES[f] + " share of world mine production",
                        f"{r['world_share_pct']:.1f}",
                        "%",
                        yr,
                        e,
                        "Calculated from the published world total.",
                    )
                )
        if evidence_holds(e, reg) or (reserve and evidence_holds(reserve, reg)):
            continue
        fields = (
            "country_iso3",
            "country_name",
            "scope",
            "commodity",
            "year",
            "production",
            "production_estimated",
            "reserves",
            "unit",
            "quantity_basis",
            "world_share_pct",
            "production_qualifier",
            "reserves_qualifier",
            "reserves_lower_bound",
            "reserves_raw",
        )
        evs = [citing(e, "production")] + ([citing(reserve, "reserves")] if reserve else [])
        tables["country_commodity"].append(
            {**{k: r.get(k) for k in fields}, **facts(e), "evidence": evs}
        )
        if r["scope"] == "country":
            tables["production"].append(
                {
                    "entity_ref": r["country_iso3"],
                    "entity_type": "country",
                    "commodity": f,
                    "year": yr,
                    "quantity": r["production"],
                    "unit": r["unit"],
                    "quantity_basis": r["quantity_basis"],
                    "production_estimated": r["production_estimated"],
                    **facts(e),
                    "evidence": [citing(e, "production")],
                }
            )
    for code, profile in minerals.items():
        rows = sorted(
            [
                r
                for r in country_rows
                if r["commodity"] == code and r["year"] == 2025 and r["scope"] == "country"
            ],
            key=lambda r: -r["production"],
        )
        china = next((r for r in rows if r["country_iso3"] == "CHN"), None)
        leaders = rows[:3]
        if china and china not in leaders:
            leaders.append(china)
        for r in leaders:
            profile["context"].append(
                fact(
                    r["country_name"] + " · world production share",
                    f"{r['world_share_pct']:.1f}",
                    "%",
                    2025,
                    r["production_evidence"],
                )
            )
        world = next(
            r
            for r in country_rows
            if r["commodity"] == code and r["scope"] == "world" and r["year"] == 2025
        )
        if china and china["reserves"] is not None:
            denominator = world["reserves"] or world["reserves_lower_bound"]
            if denominator:
                share = 100 * china["reserves"] / denominator
                value = f"{share:.1f}%"
                if world["reserves_lower_bound"] is not None:
                    value = f"Less than {ceil(share * 10) / 10:.1f}%"
                profile["context"].append(
                    fact(
                        "China’s share of reported world reserves",
                        value,
                        "",
                        2025,
                        china["reserves_evidence"],
                        "Uses the world reserve total or its published lower bound.",
                    )
                )
    restrictions = stage(root, CONTEXT, "context", "export_restrictions")
    finance = stage(root, CONTEXT, "context", "china_official_finance")
    add_policy_context(countries, country, restrictions, finance, fact, tables, reg)
    used = {r["source_id"] for table in tables.values() for r in table}
    used.update(
        e["source_id"] for t in tables.values() for r in t for e in (r.get("evidence") or [])
    )
    for sid in sorted(used):
        s = reg[sid]
        if not s["redistribution_ok"]:
            raise ValueError("A restricted source reached distributable products")
        doc_facts = [
            e
            for tab in tables.values()
            for r in tab
            for e in (r.get("evidence") or [])
            if e["source_id"] == sid
        ]
        tables["source"].append(
            {
                "source_id": sid,
                "name": s["title"],
                "publisher": s.get("publisher"),
                "url": s["url"],
                "license": s["license"],
                "redistribution_ok": True,
                "currency_date": s.get("currency"),
                "retrieved_at": s.get("retrieved_at"),
                "checksum": s.get("source_manifest_sha256")
                or s.get("checksum")
                or doc_facts[0]["raw_sha256"],
                "checksum_basis": "snapshot_manifest"
                if s.get("source_manifest_sha256")
                else "document_bytes",
                "attribution": s.get("attribution") or s["title"],
                "evidence_grade": s.get("grade"),
            }
        )
    visible_sources = {r["source_id"] for r in observations} | {
        "usgs_mcs_2026",
        "oecd_export",
        "aiddata_transition_v2",
    }
    visible_sources.update(e["source_id"] for r in visible for e in r["evidence"])
    visible_sources.update(
        e["source_id"]
        for profile in [*countries.values(), *minerals.values()]
        for e in profile["context"]
    )
    catalog = {
        "schema_version": "atlas-private-release-v1",
        "release_tag": TAG,
        "data_as_of": "2026-11-04",
        "sites": visible,
        "countries": sorted(countries.values(), key=lambda p: p["name"]),
        "minerals": list(minerals.values()),
        "sources": [reg[s] for s in sorted(visible_sources)],
        "downloads": [],
    }
    return (catalog, tables, production_review) if with_audit else (catalog, tables)


def add_policy_context(countries, country, restrictions, finance, fact, tables, reg):
    grouped = defaultdict(list)
    for r in restrictions:
        code = r["country_iso3"]
        ev = r["source_evidence"]
        if pycountry.countries.get(alpha_3=code):
            grouped[code].append(r)
        if evidence_holds(ev, reg):
            continue
        descriptions = r["descriptions"]
        fields = {
            "value": "VAL",
            "unit": "VAL_UNIT",
            "start_date_raw": "DATI",
            "end_date_raw": "END",
            "value_condition": "VAL_CONDITION",
        }
        attrs = {k: descriptions.get(key, {}).get("value") for k, key in fields.items()}
        refs = [citing(ev, "annual measure observation")]
        for k, field in fields.items():
            if field in descriptions:
                ref = descriptions[field]
                refs.append(
                    {
                        **citing(ev, k),
                        "record_id": ref["record_id"],
                        "source_row": ref["source_row"],
                    }
                )
        for f in r["commodity_families"]:
            tables["export_restriction"].append(
                {
                    "country_iso3": code,
                    "commodity": f,
                    "year": r["year"],
                    "commodity_code": r["product_code"],
                    "measure_type": r["measure_type"],
                    "variation": r["variation"],
                    **attrs,
                    **facts(ev),
                    "evidence": refs,
                }
            )
    for code, rows in grouped.items():
        last = max(r["year"] for r in rows)
        latest = [r for r in rows if r["year"] == last]
        types = sorted({r["measure_type"] for r in latest})
        country(code)["context"].append(
            fact(
                "Export measures recorded",
                ", ".join(types),
                "",
                last,
                latest[0]["source_evidence"],
                "OECD historical inventory for listed mineral products.",
            )
        )
    by_country = defaultdict(list)
    for r in finance:
        code = r["country_iso3"]
        ev = r["source_evidence"]
        if code and pycountry.countries.get(alpha_3=code):
            by_country[code].append(r)
        if evidence_holds(ev, reg):
            continue
        tables["china_official_finance"].append(
            {
                "project_id": r["aiddata_record_id"],
                "country_iso3": code,
                "commitment_year": r["year"],
                "title": r["title"],
                "amount": r["amount_constant_usd_2023"],
                "currency": "USD",
                "amount_basis": "Constant 2023 USD",
                **{
                    k: r.get(k)
                    for k in (
                        "amount_estimated",
                        "recommended_for_aggregates",
                        "source_status",
                        "flow_type",
                    )
                },
                "lender": r["lender"],
                "commodities": r["commodity_families"],
                "site_id": None,
                **facts(ev),
                "evidence": [citing(ev, "reported financial contribution")],
            }
        )
    for code, rows in by_country.items():
        latest = max(rows, key=lambda r: int(r["year"] or 0))
        years = [r["year"] for r in rows if r["year"] is not None]
        country(code)["context"].append(
            fact(
                "Recorded financing contributions",
                len(rows),
                "",
                None,
                latest["source_evidence"],
                f"AidData records for this country, {min(years)}–{max(years)}."
                if years
                else "AidData country-level records.",
            )
        )
