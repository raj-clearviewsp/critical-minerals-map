"""Apply the highest supported rung independently from dated operating status."""

from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from pathlib import Path

from pipeline.normalize.core import sha256
from pipeline.release_enrichment import corrections
from pipeline.release_enrichment.common import (
    IDENTITY,
    PILOT,
    RELEASE,
    STAGES,
    bound,
    load,
    pilot_rows,
    provenance,
    seal,
)
from pipeline.release_identity.core import check as check_identity
from pipeline.scaffold import digest, json_bytes

AS_OF = "2026-11-04"
RECENT_FROM = "2024-11-04"
LABELS = {
    1: "Occurrence",
    2: "Historical or non-compliant estimate",
    3: "Code-compliant resource",
    4: "Reserve",
    5: "Operating",
}
STAGE = "maturity"
FOLDER = f"{RELEASE}/{STAGE}"
SAMPLE_SEED = "atlas-2026.11.04-v0-maturity-review-v1"
MAP = {
    "ga_critical_2025": "Explicit 2025 operating, care-and-maintenance and development status.",
    "ga_critical_2025_arcgis": "Same GA snapshot and terminology as the workbook.",
    "ga_global_ree": (
        "Quantified deposit tonnage and mineral grade support a historical "
        "estimate. The combined resources-or-production flag alone cannot "
        "identify a resource class."
    ),
    "usgs_global_ree": (
        "Paired tonnage/grade or explicitly historical estimates. Preserve "
        "reporting codes and estimate dates. Older codes or production labels "
        "alone cannot establish current resources or activity."
    ),
    "usgs_global_critical": (
        "Occurrence evidence. Commodity and deposit-type listings alone "
        "contain no quantity or current operating date."
    ),
    "usgs_us_critical": (
        "Use the actual resource and production text. Rank indicates a "
        "category only. A dated completed production interval may establish "
        "recent production."
    ),
    "usgs_laterites": (
        "Match resource, reserve or total tonnage to its corresponding grade. "
        "Retain historical survey date and code. Future startup dates are "
        "projections."
    ),
    "gseu_crm": (
        "Keep named code, class and quantitative statement. Explicit national "
        "categories and UNFC stay distinct. The compilation date is not the "
        "estimate effective date."
    ),
    "usgs_mrds": (
        "Occurrence or documented historical producer status. The compact "
        "export has no quantified resource tables."
    ),
    "wapha": "Activity is a dated pre-August-2022 catalogue observation.",
    "jasansky": "Production through 2021 and dated status are historical observations.",
    "icmm": (
        "The July 2026 workbook defines a mine as current or past extraction. "
        "Asset type or confidence alone supplies no dated operating claim."
    ),
}
COLUMNS = [
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
    "raw_name",
    "record_kind",
    "country",
    "raw_fields",
    "raw_status",
    "raw_size_grade_text",
    "raw_commodities",
]
DATE_BY_SOURCE = {
    "ga_critical_2025": "2025-12",
    "ga_critical_2025_arcgis": "2025-12",
    "gseu_crm": "2024-10",
    "ga_global_ree": "2023",
    "icmm": "2026-07-22",
    "usgs_us_critical": "2026-09-09",
    "usgs_laterites": "2011",
    "usgs_global_ree": "2018",
    "usgs_global_critical": "2017",
    "usgs_mrds": "2011",
    "wapha": "2022-08-29",
    "jasansky": "2021",
}
STATUSES = {
    "operating mine": "operating",
    "mine - care and maintenance": "care_and_maintenance",
    "mine - under development": "under_development",
    "underdevelopment": "under_development",
    "feasibility": "feasibility",
    "pendingapproval": "pending_approval",
    "operating": "operating",
    "operatingintermittently": "intermittent_operation",
    "notoperating": "not_operating",
    "closed": "closed",
    "abandoned": "abandoned",
    "not exploited": "not_exploited",
    "past producer": "past_producer",
    "past byproduct producer": "past_producer",
    "producer": "historical_producer",
    "byproduct producer": "historical_producer",
    "no production": "no_production_reported",
    "active": "historically_active",
    "inactive": "historically_inactive",
    "exploration": "exploration",
}
CODES = {
    "JORCCode": "JORC",
    "NI43-101": "NI 43-101",
    "PERCCode": "PERC",
    "CIMstandards": "CIM",
    "RussianNAENCode": "Russian NAEN",
    "PolishClassification": "Polish classification",
    "UNFCCode": "UNFC",
    "NI43-101, UNFCCode": "NI 43-101 / UNFC",
}
CHAPTER_FILE = Path(__file__).with_name("mcs_chapters.json")
STATEMENT_FILE = Path(__file__).with_name("site_statements.json")


def statement_map():
    return {rid: fact for fact in load(STATEMENT_FILE) for rid in fact["record_ids"]}


def nonzero(value):
    if isinstance(value, (float, int)) and not isinstance(value, bool):
        return value > 0
    return bool(re.search(r"(?<!\d)[1-9]\d*(?:\.\d+)?|(?<!\d)0\.\d*[1-9]", str(value or "")))


def _fields(raw, keys):
    return {k: raw[k] for k in keys if raw.get(k) not in (None, "", " ")}


def _status(row, raw):
    s = row["source_id"]
    key = {
        "ga_critical_2025": "Status",
        "ga_critical_2025_arcgis": "status",
        "gseu_crm": "Deposit mining activity status",
        "wapha": "Status",
        "usgs_mrds": "dev_stat",
        "usgs_global_ree": "P_Status",
        "usgs_laterites": "Status",
        "jasansky": "activity_status",
        "usgs_us_critical": "depcat",
    }.get(s)
    text = str(raw.get(key) or "").strip().casefold()
    status = STATUSES.get(text)
    if s == "usgs_us_critical" and "past producer" in text.replace("-", " "):
        status = "past_producer"
    observed = (
        str(int(raw["activity_status_year"]))
        if s == "jasansky" and raw.get("activity_status_year")
        else DATE_BY_SOURCE[s]
    )
    if status in {"operating", "intermittent_operation"} and observed < RECENT_FROM:
        status = "historically_operating"
    if status is None:
        return None
    return (
        status,
        observed,
        provenance(
            row,
            _fields(raw, [key, "activity_status_year"]),
            "source_status",
            "Operating status reflects the dated source description.",
        ),
    )


def _estimate(row, raw):
    s = row["source_id"]
    code = None
    national = False
    keys = []
    rung = 1
    reason = (
        "The source identifies the site and its pilot mineral. A higher "
        "maturity rung requires additional evidence."
    )
    if s == "gseu_crm":
        for kind, amounts in [
            ("resource", "Resources - tonnage of commodity (t)"),
            ("reserve", "Reserves - tonnage of commodity (t)"),
        ]:
            method = raw.get(kind + " classification method")
            category = raw.get(kind + " category")
            if not nonzero(raw.get(amounts)) or not category:
                continue
            keys.extend([amounts, kind + " classification method", kind + " category"])
            code = CODES.get(method, code)
            national = (
                national or str(category).startswith("(RUS)") or method == "PolishClassification"
            )
            rung = 2
            reason = (
                "The compilation reports a quantified classified estimate. Its "
                "effective date and continuing validity are unverified."
            )
    elif s == "ga_global_ree":
        ton = "Tonnage (Mt)"
        grades = [
            k for k, v in raw.items() if (k.endswith("(%)") or k.endswith("(ppm)")) and nonzero(v)
        ]
        if nonzero(raw.get(ton)) and grades:
            rung = 2
            keys = [
                ton,
                *grades,
                "Source(s) of data - resources/endowment",
                "CRIRSCO-compliant resources or production- REEs",
            ]
            reason = (
                "The compilation gives a tonnage and a grade. Its combined "
                "code-or-production flag does not identify a current classified "
                "resource."
            )
    elif s == "usgs_global_ree":
        tonnes = [
            k
            for k in ("RR_Ore_Mt", "RR_HM_Mt", "RR_TREO_Mt", "RR_min_MT", "RR_mon_Mt")
            if nonzero(raw.get(k))
        ]
        grades = [
            k
            for k in (
                "RR_REE_grd",
                "RR_TREOgrd",
                "RR_HM_pct",
                "RR_min_pct",
                "RR_mon_pct",
                "RR_oth_grd",
            )
            if nonzero(raw.get(k))
        ]
        reg = str(raw.get("RR_RegCode") or "").strip()
        code = {
            "JORC compliant": "JORC",
            "NI 43-101 compliant": "NI 43-101",
            "NAEN compliant": "NAEN",
        }.get(reg)
        if tonnes and grades:
            rung = 2
            reason = (
                "The historical inventory records an estimate and grade. Its "
                "statement has no verified current effective date."
            )
        keys = [*tonnes, *grades, "RR_RegCode", "RR_Yr_Est", "RR_Note", "RR_Refs"]
    elif s == "usgs_laterites":
        for ton, grade_keys, category in [
            ("ResourceTonnage", ["Nire grade%", "Co re grade%"], "ResourceStandard"),
            ("ReserveTonnage", ["Ni rv grade%", "Co rv grade%", "Ni rs grade%"], "ReserveStandard"),
            ("TotalTonnage", ["Ni to grade%", "Co to grade%"], "ResourceStandard"),
        ]:
            graded = [k for k in grade_keys if nonzero(raw.get(k))]
            if nonzero(raw.get(ton)) and (graded or "historic" in str(raw.get(category)).lower()):
                keys.extend([ton, *graded, category, "Source TonnGrade"])
                rung = 2
                reason = (
                    "A dated historical inventory gives a quantified estimate and grade "
                    "or explicitly identifies a historical estimate."
                )
                standard = str(raw.get(category) or "").upper()
                for term, name in [
                    ("JORC", "JORC"),
                    ("NI 43-101", "NI 43-101"),
                    ("SAMREC", "SAMREC"),
                ]:
                    if term in standard:
                        code = name
    elif s == "usgs_us_critical":
        keys = [f"resources{i}" for i in range(1, 5) if raw.get(f"resources{i}")]
        for k in keys:
            t = str(raw[k])
            if re.search(
                r"\d[\d,.]*\s*(?:Mst|Mt|Mtpa|million\s+(?:metric\s+)?tons?|"
                r"million\s+tonnes?|tons?|tonnes?|short tons)\b",
                t,
                re.I,
            ) and re.search(r"\d[\d.,]*\s*(?:%|ppm|g/t|percent|oz/ton)", t, re.I):
                rung = 2
                reason = (
                    "The USGS resource text reports tonnage and grade. Its rank describes "
                    "a resource/production category and does not certify a current "
                    "reserve."
                )
                break
    present = {"raw_name": row["raw_name"], "raw_commodities": row["raw_commodities"]}
    chosen = {**present, **_fields(raw, keys)}
    ev = provenance(row, chosen, "documented_estimate" if rung > 1 else "reported_presence", reason)
    return rung, ev, code, national


def _recent_production(row, raw, chapters):
    if row["source_id"] != "usgs_us_critical":
        return None
    name = row["raw_name"].casefold().strip()
    slug = {
        ("usgs_us_critical:deposits:628", "eagle mine"): "Ni",
        ("usgs_us_critical:deposits:339", "mountain pass deposit"): "REE",
    }.get((row["record_id"], name))
    if slug:
        chapter = chapters[slug]
        ev = provenance(
            row,
            {"deposit_na": row["raw_name"], "source_country": row["country"]},
            "recent_chapter_production",
            "USGS explicitly describes 2025 mining at this named U.S. site.",
        )
        ev["verified_document"] = {
            k: chapter[k]
            for k in (
                "title",
                "url",
                "sha256",
                "retrieved_at",
                "evidence_grade",
                "redistribution_ok",
            )
        }
        ev["verified_document"]["locator"] = "Chapter page 1, Domestic Production and Use"
        ev["production_year"] = 2025
        return ev
    for key in ("prduction1", "prduction2", "prduction3"):
        text = str(raw.get(key) or "")
        if re.search(r"Production\s*\(\s*\d{4}\s*[-–]\s*2025\s*\)", text, re.I) and nonzero(
            text.split(")")[-1]
        ):
            return provenance(
                row,
                {key: text},
                "recent_reported_production",
                "The source records production through 2025 with a positive quantity.",
            )
    return None


def assess_site(site, members, chapters, statements=None):
    estimates = []
    statuses = []
    produced = []
    systems = []
    for row in sorted(members, key=lambda r: r["record_id"]):
        raw = row["raw_fields"]
        rung, ev, code, national = _estimate(row, raw)
        statement = (statements or {}).get(row["record_id"])
        if statement:
            if statement["site"] != row["raw_name"] or not statement["category_values"]:
                raise ValueError("Verified statement no longer matches its exact source site")
            rung = 4 if statement["classification"] == "proved_and_probable_reserve" else 3
            code = statement["reporting_system"]
            ev = provenance(
                row,
                {"raw_name": row["raw_name"]},
                "exact_site_technical_statement",
                f"The {statement['verified_as_of']} statement identifies a {code} "
                + ("proved and probable reserve." if rung == 4 else "classified mineral resource."),
            )
            ev["verified_document"] = {k: v for k, v in statement.items() if k != "record_ids"}
            ev["redistribution_ok"] = bool(
                row["redistribution_ok"] and statement["redistribution_ok"]
            )
        estimates.append((rung, ev))
        if code:
            systems.append((code, national, ev))
        status = _status(row, raw)
        if status:
            statuses.append(status)
        production = _recent_production(row, raw, chapters)
        if production:
            produced.append(production)
    if not estimates:
        raise ValueError("Site has no physical source evidence")
    rung, ev = max(
        estimates,
        key=lambda v: (v[0], v[1]["evidence_grade"] == "A", v[1]["evidence_grade"] == "B"),
    )
    status, when, sev = "unknown", None, None
    if statuses:
        when = max(v[1] for v in statuses)
        newest = [v for v in statuses if v[1] == when]
        status, _, sev = newest[0]
        if len({v[0] for v in newest}) > 1:
            status = "conflicting_reports"
        if status == "operating" and when >= RECENT_FROM:
            rung, ev = 5, sev
    if produced:
        rung, ev = 5, produced[0]
        if status == "unknown" or (when or "") < "2025":
            status, when, sev = "production_reported", "2025", ev
    return {
        "site_id": site["site_id"],
        "maturity_rung": rung,
        "maturity_label": LABELS[rung],
        "as_of": AS_OF,
        "status": status,
        "status_as_of": when,
        "reporting_system": " / ".join(sorted({s[0] for s in systems})) or None,
        "national_system": any(s[1] for s in systems),
        "rung_evidence": ev,
        "status_evidence": sev,
        "reporting_evidence": [s[2] for s in systems],
        "status_observations": [{"status": s, "as_of": w, "evidence": e} for s, w, e in statuses],
        "limitations": [ev["reason"]],
        "source_record_ids": sorted(row["record_id"] for row in members),
    }


def inputs(root):
    ident = check_identity(root)
    if ident.get("status") != "reviewed_manual_release":
        raise ValueError("Step 6 has not been accepted")
    sites = bound(root, ident, f"{RELEASE}/identity/sites.json")
    members = bound(root, ident, f"{RELEASE}/identity/members.json")
    by_source = defaultdict(set)
    by_site = defaultdict(list)
    for m in members:
        by_source[m["source_id"]].add(m["record_id"])
        by_site[m["site_id"]].append(m["record_id"])
    rows = {}
    for source, ids in sorted(by_source.items()):
        registry = load(root / f"sources/{source}.yaml")
        for r in pilot_rows(root, source, COLUMNS, ids):
            for k in ("raw_fields", "raw_status", "raw_size_grade_text", "raw_commodities"):
                r[k] = json.loads(r[k])
            r["redistribution_ok"] = registry["redistribution_ok"]
            rows[r["record_id"]] = r
    if set(rows) != set.union(*by_source.values()):
        raise ValueError("Missing pilot member evidence")
    if len({s["site_id"] for s in sites}) != len(sites) or set(by_site) != {
        s["site_id"] for s in sites
    }:
        raise ValueError("Identity membership does not cover the site table")
    return sites, {k: [rows[i] for i in v] for k, v in by_site.items()}


def spot_packet(sites, by_site):
    chosen = sorted(sites, key=lambda s: digest(f"{SAMPLE_SEED}:{s['site_id']}".encode()))[:30]
    if len(chosen) != 30:
        raise ValueError("Spot check needs 30 distinct eligible sites")
    cases = []
    statements = statement_map()
    chapters = load(CHAPTER_FILE)
    for site in chosen:
        observations = []
        for r in by_site[site["site_id"]]:
            # Independent reviewer sees relevant native fields and provenance, never model
            # assignments.
            observations.append(
                {k: v for k, v in r.items() if k != "raw_fields"}
                | {
                    "additional_primary_sources": (
                        [statements[r["record_id"]]]
                        if r["record_id"] in statements
                        else [
                            {
                                k: chapters[family][k]
                                for k in ("title", "url", "locator", "retrieved_at", "sha256")
                            }
                            for record, family in (
                                ("usgs_us_critical:deposits:628", "Ni"),
                                ("usgs_us_critical:deposits:339", "REE"),
                            )
                            if record == r["record_id"]
                        ]
                    ),
                    "source_fields": {
                        k: v
                        for k, v in r["raw_fields"].items()
                        if k != "_linked_tables"
                        and k not in ("linked_tables", "source_geometry")
                        and v not in (None, "", " ")
                    },
                }
            )
        cases.append(
            {"site_id": site["site_id"], "name": site["name"], "observations": observations}
        )
    result = {
        "schema_version": "maturity-blind-source-packet-v1",
        "seed": SAMPLE_SEED,
        "as_of": AS_OF,
        "cases": cases,
    }
    result["sample_sha256"] = digest(json_bytes(result))
    return result


def validate_assessment(packet, result, maturity):
    if (
        result.get("schema_version") != "independent-maturity-assessment-v1"
        or result.get("sample_sha256") != packet["sample_sha256"]
        or not isinstance(result.get("reviewer"), str)
        or not result["reviewer"].strip()
        or result.get("blinded_to_proposed_rungs") is not True
    ):
        raise ValueError("Independent assessment must bind the blind packet and reviewer")
    entries = result.get("assessments", [])
    indexed = {e["site_id"]: e for e in entries}
    if len(entries) != 30 or set(indexed) != {c["site_id"] for c in packet["cases"]}:
        raise ValueError("Assessment must cover the 30 selected sites exactly")
    by_id = {r["site_id"]: r for r in maturity}
    outcomes = []
    for c in packet["cases"]:
        ref = indexed[c["site_id"]]
        if (
            type(ref.get("rung")) is not int
            or ref["rung"] not in LABELS
            or ref.get("record_id") not in {r["record_id"] for r in c["observations"]}
            or not isinstance(ref.get("reason"), str)
            or len(ref["reason"].strip()) < 15
        ):
            raise ValueError("Assessment requires a supported rung, source record and rationale")
        assigned = by_id[c["site_id"]]["maturity_rung"]
        outcomes.append(
            {
                "site_id": c["site_id"],
                "assigned_rung": assigned,
                "reviewer_rung": ref["rung"],
                "agree": assigned == ref["rung"],
                "reviewer_record_id": ref["record_id"],
                "reviewer_reason": ref["reason"],
            }
        )
    return outcomes


def build(
    root: Path, assessment: Path, primary: Path | None = None, correction_review: Path | None = None
):
    sites, by_site = inputs(root)
    chapters = load(CHAPTER_FILE)
    statements = statement_map()
    primary = primary or root / FOLDER / "primary"
    rows = [
        assess_site(s, by_site[s["site_id"]], chapters, statements)
        for s in sorted(sites, key=lambda x: x["site_id"])
    ]
    packet = spot_packet(sites, by_site)
    original = assessment.read_bytes()
    results = validate_assessment(packet, json.loads(original), rows)
    archive_audit = corrections.audit(root, by_site)
    confirmation = (
        correction_review or root / FOLDER / "reserve-independent-review.json"
    ).read_bytes()
    reviewed, changes = corrections.overlay(rows, archive_audit, json.loads(confirmation))
    agree = sum(r["agree"] for r in results)
    status = "accepted" if agree >= 27 else "spot_check_failed"
    receipt = {
        "schema_version": "release-maturity-v1",
        "step": 7,
        "status": status,
        "parent_receipt_sha256": sha256(root / IDENTITY),
        "pilot_receipt_sha256": sha256(root / PILOT),
        "sites": len(rows),
        "rung_counts": dict(sorted(Counter(str(r["maturity_rung"]) for r in rows).items())),
        "status_counts": dict(sorted(Counter(r["status"] for r in rows).items())),
        "spot_check_cases": 30,
        "spot_check_agreements": agree,
        "spot_check_passed": agree >= 27,
        "sample_sha256": packet["sample_sha256"],
        "assessment_sha256": digest(original),
        "scored_sites_sha256": digest(json_bytes(rows)),
        "reviewed_rung_counts": dict(
            sorted(Counter(str(r["maturity_rung"]) for r in reviewed).items())
        ),
        "post_assessment_corrections": len(changes),
        "reserve_archive_sha256": archive_audit["archive_sha256"],
        "reserve_review_sha256": digest(confirmation),
    }
    files = {
        f"{FOLDER}/sites.json": json_bytes(rows),
        f"{FOLDER}/reviewed-sites.json": json_bytes(reviewed),
        f"{FOLDER}/corrections.json": json_bytes(changes),
        f"{FOLDER}/jasansky-reserve-audit.json": json_bytes(archive_audit),
        f"{FOLDER}/reserve-independent-review.json": confirmation,
        f"{FOLDER}/mapping.json": json_bytes(MAP),
        f"{FOLDER}/spot-check-input.json": json_bytes(packet),
        f"{FOLDER}/spot-check-assessment.json": original,
        f"{FOLDER}/spot-check-results.json": json_bytes(results),
    }
    proof_docs = {chapters[f]["file"]: chapters[f]["sha256"] for f in ("Ni", "REE")}
    proof_docs["arafura-annual-2026.pdf"] = next(
        s["sha256"] for s in statements.values() if s["site"] == "Nolans Bore"
    )
    for name, expected in proof_docs.items():
        raw = (primary / name).read_bytes()
        if digest(raw) != expected:
            raise ValueError(f"Verified source document changed: {name}")
        files[f"{FOLDER}/primary/{name}"] = raw
    receipt["verified_document_sha256"] = proof_docs
    report = f"""# Phase 0 Step 7. Maturity and operating status

Every one of the {len(rows):,} identity-release sites has a maturity rung, a source record and
an explanation.
Status is dated separately. Historical production, a facility label and a compilation retrieval
date cannot establish current production.
Older estimates retain their reported codes and national classifications. Missing estimate
dates remain explicit.

The independent reviewer assessed a reproducible random sample of 30 sites using original
source fields. {agree} assignments agreed. The required threshold is 27. The recorded result is
{status}.

The scored assignments are preserved in sites.json. One independent reviewer identified a
historical estimate in the underlying Jasansky reserve table. A separate archive-wide audit
checked {archive_audit["eligible_archive_rows"]} reserve rows linked to
{archive_audit["eligible_facilities"]} eligible member facilities. Independent verification
confirmed the qualifying native rows, years,
tonnes and grades. The reviewed-sites.json view applies {len(changes)} corrections for display.
The original blind score remains {agree}/30. Each correction retains its source observation,
archive checksum, native row ID, physical CSV row and original citation.
"""
    files[STAGES[STAGE][1]] = report.encode()
    return files, receipt


def prepare(root, assessment, primary=None, correction_review=None):
    files, receipt = build(root, assessment, primary, correction_review)
    return seal(root, STAGE, files, receipt)
