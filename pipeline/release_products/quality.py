"""Validate typed release data and withhold rows that fail a distribution check."""

from __future__ import annotations

import re
from collections import Counter
from datetime import date, datetime
from pathlib import Path
from urllib.parse import urlsplit

import pandas as pd
import pandera.pandas as pa
import yaml

from .common import BASE, CONTEXT, IDENTITY, MATURITY, QA, bound, encoded, load, seal
from .geography import boundaries, check_point

FILE = f"{BASE}/qa/dispositions.json"
REPORT = "reports/phase0-step09-qa.md"


def safe_url(value):
    if not isinstance(value, str):
        return False
    u = urlsplit(value)
    return u.scheme in ("https", "http") and bool(u.hostname) and not (u.username or u.password)


def known_date(value):
    try:
        if not isinstance(value, str):
            return False
        if len(value) == 10:
            date.fromisoformat(value)
        else:
            datetime.fromisoformat(value.replace("Z", "+00:00"))
        return True
    except (TypeError, ValueError):
        return False


def source_registry(root):
    result = {}
    accepted = {
        s["source_id"]: s for s in load(root, "reports/normalization-audit.json")["sources"]
    }
    for path in sorted((root / "sources").glob("*.yaml")):
        s = yaml.safe_load(path.read_bytes())
        record = accepted.get(s["source_id"])
        m = {}
        manifest_path = None
        checksum = None
        if record:
            snapshots = {
                Path("data/raw", *Path(f).parts[:2], "manifest.json") for f in record["files"]
            }
            if len(snapshots) != 1:
                raise ValueError("Normalization must bind one source snapshot")
            manifest_path = str(snapshots.pop())
            raw = (root / manifest_path).read_bytes()
            from .common import sha

            checksum = sha(raw)
            if checksum != record["manifest_sha256"]:
                raise ValueError("Changed source snapshot manifest")
            m = load(root, manifest_path)
        result[s["source_id"]] = {
            "id": s["source_id"],
            "title": s["name"],
            "publisher": s["publisher"],
            "url": s["url"],
            "license": s["license"],
            "attribution": s.get("attribution"),
            "redistribution_ok": s["redistribution_ok"] is True
            and s.get("license_status") == "confirmed_open",
            "retrieved_at": m.get("retrieved_at"),
            "registered_at": m.get("registered_at"),
            "currency": s.get("currency"),
            "grade": s.get("evidence_grade"),
            "raw_manifest_path": manifest_path,
            "source_manifest_sha256": checksum,
        }
    return result


def evidence_holds(evidence, registry):
    """Each underlying observation must qualify separately. A newer citation cannot clear it."""
    reasons = set()
    if not evidence or not isinstance(evidence, dict):
        return {"missing_evidence"}
    sid = evidence.get("source_id")
    src = registry.get(sid, {})
    if src.get("redistribution_ok") is not True or evidence.get("redistribution_ok") is False:
        reasons.add("source_terms")
    if not known_date(evidence.get("retrieved_at")):
        reasons.add("retrieval_date")
    if not safe_url(evidence.get("source_url")):
        reasons.add("source_url")
    if evidence.get("evidence_grade") not in ("A", "B", "C", "D"):
        reasons.add("evidence_grade")
    if not re.fullmatch("[0-9a-f]{64}", str(evidence.get("raw_sha256", ""))):
        reasons.add("source_checksum")
    document = evidence.get("verified_document")
    if document:
        if document.get("redistribution_ok") is not True:
            reasons.add("document_terms")
        if not known_date(document.get("retrieved_at")):
            reasons.add("document_retrieval")
        if not safe_url(document.get("url")) or not re.fullmatch(
            "[0-9a-f]{64}", str(document.get("sha256", ""))
        ):
            reasons.add("document_evidence")
    return reasons


def table(rows, required, unique=()):
    schema = pa.DataFrameSchema(required, unique=list(unique) or None, strict=False)
    frame = pd.DataFrame(rows) if rows else pd.DataFrame(columns=list(required))
    schema.validate(frame, lazy=True)


def stage(root, receipt, section, name):
    return bound(root, receipt, f"{BASE}/{section}/{name}.json")


def inputs(root):
    return (
        stage(root, IDENTITY, "identity", "sites"),
        stage(root, IDENTITY, "identity", "members"),
        stage(root, IDENTITY, "identity", "observations"),
        stage(root, MATURITY, "maturity", "reviewed-sites"),
    )


def assess(root):
    sites, members, observations, maturity = inputs(root)
    key = pa.Column(str, nullable=False)
    table(
        sites,
        {
            "site_id": key,
            "name": key,
            "lat": pa.Column(float, pa.Check.in_range(-90, 90)),
            "lon": pa.Column(float, pa.Check.in_range(-180, 180)),
        },
        ("site_id",),
    )
    table(observations, {"record_id": key, "source_id": key, "source_url": key}, ("record_id",))
    table(
        members,
        {
            "record_id": key,
            "site_id": key,
            "method": pa.Column(str, pa.Check.isin(["manual", "source_observation"])),
        },
        ("record_id",),
    )
    table(
        maturity,
        {"site_id": key, "maturity_rung": pa.Column(int, pa.Check.in_range(1, 5)), "status": key},
        ("site_id",),
    )
    obs = {r["record_id"]: r for r in observations}
    grades = {r["site_id"]: r for r in maturity}
    membership = {r["record_id"]: r["site_id"] for r in members}
    if set(grades) != {s["site_id"] for s in sites} or len(obs) != 10788:
        raise ValueError("Incomplete site enrichment or source observation reconciliation")
    registry = source_registry(root)
    shapes = boundaries(root)
    checks = []
    repeated = Counter(
        (s["name"].casefold(), round(s["lat"], 5), round(s["lon"], 5)) for s in sites
    )
    for site in sites:
        key = site["site_id"]
        ids = site["record_ids"]
        anchor = obs[site["coordinate_record_id"]]
        if not ids or anchor["record_id"] not in ids or any(membership.get(r) != key for r in ids):
            raise ValueError("Invalid site membership or anchor")
        if (site["lat"], site["lon"]) != (anchor["lat"], anchor["lon"]):
            raise ValueError("Representative point differs from its source")
        geo = check_point(site["lon"], site["lat"], site["country_iso3"], shapes)
        reasons = set()
        if geo not in ("inside_country", "within_25_km"):
            reasons.add(geo)
        mat = grades[key]
        for rid in ids:
            row = obs[rid]
            member_geo = check_point(row["lon"], row["lat"], row["country"], shapes)
            if member_geo not in ("inside_country", "within_25_km"):
                reasons.add("member_" + member_geo)
            evidence = {
                **row["evidence"],
                "source_id": row["source_id"],
                "source_url": row["source_url"],
            }
            if not safe_url(row["source_url"]) or evidence.get("evidence_grade") not in (
                "A",
                "B",
                "C",
                "D",
            ):
                raise ValueError("Missing source citation or grade for site member")
            reasons |= evidence_holds(evidence, registry)
        for ev in [
            mat["rung_evidence"],
            mat.get("status_evidence"),
            *mat.get("reporting_evidence", []),
        ]:
            if ev is None:
                continue
            if ev.get("record_id") not in ids or not safe_url(ev.get("source_url")):
                raise ValueError("Maturity or status evidence lost its source member")
            reasons |= evidence_holds(ev, registry)
        checks.append(
            {
                "site_id": key,
                "geography": geo,
                "download_holds": sorted(reasons),
                "geographic_pass": geo in ("inside_country", "within_25_km"),
                "coincident_named_entries": repeated[
                    (site["name"].casefold(), round(site["lat"], 5), round(site["lon"], 5))
                ],
                "distributable": not reasons,
            }
        )
    stat = Counter(r["geography"] for r in checks)
    holds = Counter(k for r in checks for k in r["download_holds"])
    # Verify all accepted context data before release.
    ctx = load(root, CONTEXT)
    context_counts = {}
    for p in ctx["artifacts"]:
        if p.startswith(f"{BASE}/context/") and p.endswith(".json"):
            rows = bound(root, CONTEXT, p)
            if isinstance(rows, list):
                context_counts[p.rsplit("/", 1)[1]] = len(rows)
    counts = {
        "sites": len(sites),
        "members": len(members),
        "observations": len(obs),
        "site_rungs": dict(sorted(Counter(str(s["maturity_rung"]) for s in maturity).items())),
        "geography": dict(sorted(stat.items())),
        "download_holds": dict(sorted(holds.items())),
        "distributable_sites": sum(r["distributable"] for r in checks),
        "context_rows": context_counts,
        "coincident_named_groups": sum(n > 1 for n in repeated.values()),
        "entries_with_publisher_counterparts": sum(
            bool(s.get("publisher_view_counterparts")) for s in sites
        ),
    }
    return checks, counts


def validate_context(root):
    key = pa.Column(str, nullable=False)
    year = pa.Column(int, pa.Check.in_range(1900, 2026))
    data = stage(root, CONTEXT, "context", "country_commodity")
    table(
        data,
        {
            "country_name": key,
            "commodity": key,
            "year": year,
            "quantity_basis": key,
            "production": pa.Column(float, pa.Check.ge(0)),
            "world_share_pct": pa.Column(float, pa.Check.in_range(0, 100)),
        },
        ("country_name", "commodity", "year"),
    )
    production = [r for r in data if r["scope"] == "country"]
    table(
        production,
        {
            "country_iso3": key,
            "commodity": key,
            "year": year,
            "production": pa.Column(float, pa.Check.ge(0)),
        },
        ("country_iso3", "commodity", "year"),
    )
    measures = stage(root, CONTEXT, "context", "export_restrictions")
    table(measures, {"source_area": key, "product_code": key, "measure_type": key, "year": year})
    finance = stage(root, CONTEXT, "context", "china_official_finance")
    table(
        finance,
        {
            "aiddata_record_id": key,
            "site_id": pa.Column(
                None, nullable=True, checks=pa.Check(lambda col: col.isna().all())
            ),
        },
        ("aiddata_record_id",),
    )
    for row in [*measures, *finance]:
        e = row["source_evidence"]
        if (
            not e.get("record_id")
            or e.get("evidence_grade") not in "ABCD"
            or not safe_url(e.get("source_url"))
        ):
            raise ValueError("Missing policy or financing provenance")
    registry = [source for source in source_registry(root).values() if source["raw_manifest_path"]]
    table(
        registry,
        {
            "id": key,
            "url": pa.Column(str, pa.Check(safe_url, element_wise=True)),
            "redistribution_ok": pa.Column(bool),
        },
        ("id",),
    )
    graded = stage(root, MATURITY, "maturity", "reviewed-sites")
    mines = [
        {
            "site_id": r["site_id"],
            "type": "mine",
            "status": r["status"],
            "source_url": r["rung_evidence"]["source_url"],
        }
        for r in graded
        if r["maturity_rung"] == 5
    ]
    table(
        mines,
        {
            "site_id": key,
            "type": key,
            "status": key,
            "source_url": pa.Column(str, pa.Check(safe_url, element_wise=True)),
        },
        ("site_id",),
    )


def write(root):
    validate_context(root)
    checks, c = assess(root)
    report = f"""# Phase 0 geographic and publication checks

Validated {c["sites"]:,} site entries and {c["members"]:,} memberships against the reviewed
register, source coordinates and dated maturity evidence. All {c["observations"]:,} observations
remain traceable. Every site ID has one maturity assignment and an explicit representative source.

Country checks used Natural Earth 1:50 million geometry with a 25 km border tolerance.
Outcomes: {c["geography"]}. Unverified locations are held from the map and downloadable sites.
The separate private source archive retains the publisher's observations for inspection.
There are {c["coincident_named_groups"]} groups with identical names and source points coinciding
to five decimal places. {c["entries_with_publisher_counterparts"]} entries have alternate publisher
views. These reviews preserve exact source identities and provisional labels.

The download policy checks every contributing source for confirmed reuse, known retrieval date,
source URL, grade and checksum. {c["distributable_sites"]:,} sites qualify. Hold reasons overlap:
{c["download_holds"]}. Detailed dispositions are private and no source coordinate was moved.

The next stage validates the schemas of the export tables and tiles after serialization.
""".encode()
    code = ["pipeline/release_products/" + x for x in ("common.py", "geography.py", "quality.py")]
    return seal(
        root,
        QA,
        {FILE: encoded(checks), REPORT: report},
        {"status": "passed", "counts": c},
        [
            IDENTITY,
            MATURITY,
            CONTEXT,
            "reports/normalization-audit.json",
            "sources/boundaries/manifest.json",
            "sources/boundaries/countries-50m.geojson.gz",
            *[str(p.relative_to(root)) for p in sorted((root / "sources").glob("*.yaml"))],
        ],
        code + ["tools/release/uv.lock"],
    )
