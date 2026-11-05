"""Reconcile exact, archived operator evidence with the sealed maturity review."""

from __future__ import annotations

import copy
import math
import re
from calendar import monthrange
from dataclasses import dataclass
from datetime import date, datetime
from html.parser import HTMLParser

import yaml

from .common import BASE, IDENTITY, bound, load, sha

AS_OF = date(2026, 11, 4)
CATALOG = "overrides/pilot_operator_evidence.json"
DOCUMENTS = "overrides/pilot_operator_documents.json"
GA_SHEET = (
    "ga_critical_2025:CM Mines and Deposits 2025:sha256:"
    "256048e789565ae1183dbc02ba3b43572a03f25404c2d50d2be56a05cec0200f:row:"
)
PUBLISHER_URLS = {
    "mp_materials_q2_2026": "https://www.sec.gov/Archives/edgar/data/1801368/000180136826000048/mp-20260630.htm",
    "talon_eagle_q2_2026": "https://talonmetals.com/talon-metals-reports-second-quarter-2026-results/",
    "bhp_olympic_dam": "https://www.sec.gov/Archives/edgar/data/811809/000119312526354647/bhp-20260630.htm",
    "lynas_mt_weld": "https://lynasrareearths.com/about-us/about-lynas-rare-earths/",
}


@dataclass(frozen=True)
class DocumentScope:
    title: str
    period: str | None
    period_end: str | None
    grade: str
    names: tuple[str, ...]
    country: str
    point: tuple[float, float]
    record_ids: tuple[str, ...]
    families: tuple[str, ...]
    passages: tuple[str, ...]
    finding: str


# The coordinates guard the identities already selected in the archived source
# investigation. They do not find new sites, estimate mine outlines or establish membership.
SCOPES = {
    "mp_materials_q2_2026": DocumentScope(
        "MP Materials Form 10-Q for the quarter ended June 30, 2026",
        "2026-06-30",
        "2026-06-30",
        "A",
        ("Mountain Pass", "Mountain Pass deposit"),
        "USA",
        (35.4794, -115.5334),
        ("usgs_us_critical:deposits:339", "usgs_global_ree:Global_REE:1343"),
        ("REE",),
        (
            "form 10-q",
            "mp materials corp.",
            "quarterly period ended june 30, 2026",
            "owns and operates the mountain pass rare earth mine",
            "san bernardino county, california",
            "reo production volume (mts) 11,072",
        ),
        "MP Materials identifies Mountain Pass as its California mine and processing facility. "
        "Its June 2026 quarterly results report 11,072 metric tonnes of REO-equivalent production.",
    ),
    "talon_eagle_q2_2026": DocumentScope(
        "Talon Metals Reports Second Quarter 2026 Results",
        "2026-06-30",
        "2026-06-30",
        "D",
        ("Eagle mine",),
        "USA",
        (46.74815, -87.89729),
        ("usgs_us_critical:deposits:628",),
        ("Ni",),
        (
            "talon metals reports second quarter 2026 results",
            "three-month period ended june 30, 2026",
            "eagle mine and humboldt mill in michigan",
            "operations at eagle mine faced challenges",
            "mine production and stope sequencing",
            "nickel in the case of eagle",
        ),
        "Talon identifies the Eagle Mine in Michigan and describes mine production and mined "
        "grades during the quarter ended June 30, 2026. Nickel is its primary metal.",
    ),
    "bhp_olympic_dam": DocumentScope(
        "BHP Form 20-F for the year ended June 30, 2026",
        "FY2026",
        "2026-06-30",
        "A",
        ("Olympic Dam",),
        "AUS",
        (-30.4403264, 136.8893697),
        ("ga_critical_2025_arcgis:layer:4:objectid:2364", GA_SHEET + "403"),
        (),
        (
            "form 20-f",
            "fiscal year ended 30 june 2026",
            "copper south australia",
            "olympic dam has integrated crushing",
            "olympic dam achieved a 20-year copper production record",
        ),
        "BHP reports copper production at Olympic Dam in South Australia for FY2026. This "
        "establishes activity at the operation. GA supplies its pilot-mineral association.",
    ),
    "lynas_mt_weld": DocumentScope(
        "About Lynas Rare Earths",
        None,
        None,
        "D",
        ("Mount Weld",),
        "AUS",
        (-28.8599486, 122.5475227),
        ("ga_critical_2025_arcgis:layer:4:objectid:2450", GA_SHEET + "365"),
        ("REE",),
        (
            "about lynas rare earths",
            "mt weld mine in western australia",
            "concentrate sourced from mt weld",
        ),
        "Lynas identifies the Mt Weld mine and its concentrate supply. Its corporate page "
        "gives no reporting period for that activity.",
    ),
}


class _VisibleText(HTMLParser):
    """Read the archived body text, excluding scripts, styles and hidden filing facts."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.ignored = None
        self.depth = 0

    def handle_starttag(self, tag, attrs):
        if self.ignored:
            if tag == self.ignored:
                self.depth += 1
            return
        attributes = dict(attrs)
        style = (attributes.get("style") or "").replace(" ", "").casefold()
        if tag in {"script", "style", "noscript", "ix:header", "ix:hidden"} or (
            tag not in {"br", "img", "input", "meta", "link", "hr"}
            and ("hidden" in attributes or "display:none" in style or "visibility:hidden" in style)
        ):
            self.ignored, self.depth = tag, 1

    def handle_endtag(self, tag):
        if tag == self.ignored:
            self.depth -= 1
            if not self.depth:
                self.ignored = None

    def handle_data(self, data):
        if not self.ignored:
            self.parts.append(data)


def _validate_document(source_id, item, document, raw):
    scope = SCOPES[source_id]
    if (
        item["source_id"] != source_id
        or document["source_id"] != source_id
        or item["title"] != scope.title
        or item["reporting_period"] != scope.period
        or item["evidence_grade"] != scope.grade
        or not item.get("locator")
        or tuple(item["production_families"]) != scope.families
        or len(item["record_ids"]) != len(scope.record_ids)
        or set(item["record_ids"]) != set(scope.record_ids)
        or document.get("access") != "archived_document"
        or document["url"] != item["url"]
        or document["final_url"] != item["url"]
        or item["url"] != PUBLISHER_URLS[source_id]
        or sha(raw) != document["sha256"]
        or len(raw) != document["bytes"]
    ):
        raise ValueError(f"Operator document, scope, period or checksum differs: {source_id}")
    retrieved = datetime.fromisoformat(document["retrieved_at"].replace("Z", "+00:00"))
    if retrieved.tzinfo is None:
        raise ValueError(f"Operator retrieval requires a time zone: {source_id}")
    if scope.period_end:
        end = date.fromisoformat(scope.period_end)
        if not AS_OF.replace(year=AS_OF.year - 2) <= end <= AS_OF or retrieved.date() < end:
            raise ValueError(f"Operator period is outside the release evidence window: {source_id}")
    parser = _VisibleText()
    parser.feed(raw.decode("utf-8-sig"))
    parser.close()
    text = " ".join(" ".join(parser.parts).casefold().split())
    if any(phrase not in text for phrase in scope.passages):
        raise ValueError(f"Archived text does not substantiate the operator claim: {source_id}")
    return {
        "source_id": source_id,
        "title": item["title"],
        "url": document["url"],
        "sha256": document["sha256"],
        "retrieved_at": document["retrieved_at"],
        "reporting_period": scope.period,
        "reporting_period_end": scope.period_end,
        "verified_as_of": scope.period_end,
        "evidence_grade": scope.grade,
        "redistribution_ok": False,
        "locator": item["locator"],
        "scope": scope.finding,
        "production_families": list(scope.families),
        "license": "Private citation to the archived operator document.",
    }


def _matches_scope(name, country, lat, lon, scope):
    if name not in scope.names or country != scope.country:
        return False
    if not all(type(x) in (int, float) and math.isfinite(x) for x in (lat, lon)):
        return False
    # Five kilometres is a rejection guard around the independently curated named locality.
    y, x = scope.point
    a = math.sin(math.radians(lat - y) / 2) ** 2
    a += (
        math.cos(math.radians(lat))
        * math.cos(math.radians(y))
        * math.sin(math.radians(lon - x) / 2) ** 2
    )
    return 2 * 6371.0 * math.asin(min(1.0, math.sqrt(a))) <= 5


def _status_precedes(status_as_of, period_end):
    """A coarse year or month must end before the new evidence to replace it."""
    if not status_as_of:
        return True
    text = str(status_as_of)
    if re.fullmatch(r"\d{4}", text):
        last_day = date(int(text), 12, 31)
    elif re.fullmatch(r"\d{4}-\d{2}", text):
        year, month = map(int, text.split("-"))
        last_day = date(year, month, monthrange(year, month)[1])
    else:
        try:
            last_day = date.fromisoformat(text)
        except ValueError:
            return False
    return last_day < date.fromisoformat(period_end)


def _apply_documents(sites, baseline_rows, observations, documents):
    effective = copy.deepcopy(baseline_rows)
    by_site = {s["site_id"]: s for s in sites}
    grades = {r["site_id"]: r for r in effective}
    if (
        len(by_site) != len(sites)
        or len(grades) != len(effective)
        or by_site.keys() != grades.keys()
    ):
        raise ValueError("Production review requires the exact baseline site register")
    members = {rid: s["site_id"] for s in sites for rid in s["record_ids"]}
    if len(members) != sum(len(s["record_ids"]) for s in sites):
        raise ValueError("A source member belongs to several sites")
    audit = {
        "schema_version": "operator-production-reconciliation-v1",
        "as_of": AS_OF.isoformat(),
        "basis": "Separate publication review after the sealed Step 7 blind assessment.",
        "source_checks": [],
        "corrections": [],
    }
    for source_id, doc in sorted(documents.items()):
        scope = SCOPES[source_id]
        checks = []
        audit["source_checks"].append({**copy.deepcopy(doc), "bindings": checks})
        for rid in scope.record_ids:
            obs = observations[rid]
            if (
                obs["record_id"] != rid
                or obs["source_id"] != rid.split(":", 1)[0]
                or not _matches_scope(obs["name"], obs["country"], obs["lat"], obs["lon"], scope)
            ):
                raise ValueError(f"Operator claim and exact source observation disagree: {rid}")
            sid = members.get(rid)
            if sid != obs["site_id"]:
                raise ValueError(
                    f"Operator source membership differs from the reviewed register: {rid}"
                )
            check = {"record_id": rid, "site_id": sid}
            checks.append(check)
            if sid is None:
                check.update(
                    outcome="no_physical_site",
                    reason="The accepted register assigns this observation no physical site.",
                )
                continue
            site, row = by_site[sid], grades[sid]
            if not _matches_scope(
                site["name"], site["country_iso3"], site["lat"], site["lon"], scope
            ):
                raise ValueError(f"Operator document is outside the accepted site's scope: {rid}")
            if (
                set(row["source_record_ids"]) != set(site["record_ids"])
                or row["as_of"] != AS_OF.isoformat()
            ):
                raise ValueError(f"Maturity baseline does not identify the accepted site: {sid}")
            prior = {
                "maturity_rung": row["maturity_rung"],
                "status": row["status"],
                "status_as_of": row["status_as_of"],
            }
            check["baseline"] = prior
            if not scope.period_end:
                check.update(outcome="undated_operator_context", reason=scope.finding)
                continue
            if row["maturity_rung"] == 5 and row["status"] in {"operating", "production_reported"}:
                check.update(outcome="corroborates_baseline", reason=scope.finding)
                continue
            # A later closure or suspension takes precedence over earlier production.
            if not _status_precedes(row.get("status_as_of"), scope.period_end):
                check.update(
                    outcome="baseline_status_same_or_later",
                    reason="The baseline status date overlaps or follows this production period.",
                )
                continue
            evidence = {
                **copy.deepcopy(obs["evidence"]),
                "record_id": rid,
                "source_id": obs["source_id"],
                "source_url": obs["source_url"],
                "redistribution_ok": False,
                "rule": "dated_operator_production_for_exact_site",
                "reason": scope.finding,
                "fields": {"site": obs["name"], "production_families": list(scope.families)},
                "production_year": int(scope.period_end[:4]),
                "verified_document": copy.deepcopy(doc),
            }
            correction = {
                "site_id": sid,
                "record_id": rid,
                "operator_source_id": source_id,
                "baseline": prior,
                "baseline_rung_evidence": copy.deepcopy(row["rung_evidence"]),
                "baseline_status_evidence": copy.deepcopy(row.get("status_evidence")),
                "effective_rung": 5,
                "effective_status": "production_reported",
                "effective_status_as_of": scope.period_end,
                "reason": scope.finding,
                "distribution_hold": "private_operator_document",
                "evidence": evidence,
            }
            audit["corrections"].append(correction)
            row.update(
                maturity_rung=5,
                maturity_label="Operating",
                rung_evidence=copy.deepcopy(evidence),
                status="production_reported",
                status_as_of=scope.period_end,
                status_evidence=copy.deepcopy(evidence),
            )
            row["status_observations"].append(
                {
                    "as_of": scope.period_end,
                    "status": "production_reported",
                    "evidence": copy.deepcopy(evidence),
                }
            )
            check.update(outcome="corrected_for_publication", reason=scope.finding)
    return effective, audit


def reviewed_for_release(root, sites, baseline_rows):
    """Return effective private rows and the correction audit. Inputs are never mutated."""
    catalog_receipt = load(root, CATALOG)
    catalog_path = f"{BASE}/evidence/operator-citations.yaml"
    raw = (root / catalog_path).read_bytes()
    if catalog_receipt.get("path") != catalog_path or sha(raw) != catalog_receipt.get("sha256"):
        raise ValueError("Changed operator evidence catalog")
    entries = yaml.safe_load(raw)
    items = {i["source_id"]: i for i in entries}
    manifest = load(root, DOCUMENTS)
    if len(items) != len(entries) or set(items) != set(SCOPES) or set(manifest) != set(SCOPES):
        raise ValueError("Incomplete or duplicate archived operator sources")
    docs = {}
    for key in sorted(SCOPES):
        raw = (root / BASE / "evidence/operators" / f"{key}.html").read_bytes()
        docs[key] = _validate_document(key, items[key], manifest[key], raw)
    wanted = {rid for scope in SCOPES.values() for rid in scope.record_ids}
    observed = bound(root, IDENTITY, f"{BASE}/identity/observations.json")
    selected = [r for r in observed if r["record_id"] in wanted]
    observations = {r["record_id"]: r for r in selected}
    if set(observations) != wanted or len(selected) != len(wanted):
        raise ValueError("Missing or duplicate exact operator source observation")
    effective, audit = _apply_documents(sites, baseline_rows, observations, docs)
    audit["operator_catalog_sha256"] = catalog_receipt["sha256"]
    audit["document_manifest_sha256"] = sha((root / DOCUMENTS).read_bytes())
    return effective, audit
