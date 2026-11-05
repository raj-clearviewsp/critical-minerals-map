"""Keep independently verified historical reserve evidence separate from scored assignments."""

from __future__ import annotations

import copy
import csv
import io
import zipfile
from collections import Counter, defaultdict
from decimal import Decimal, InvalidOperation
from pathlib import Path

from pipeline.release_enrichment.common import provenance
from pipeline.scaffold import digest

RESERVES = "data/reserves.csv"
SOURCES = "data/source_ids.csv"
PILOT_METALS = {
    "Me.Ni",
    "Me.Co",
    "Me.REE",
    "Me.La",
    "Me.Ce",
    "Me.Pr",
    "Me.Nd",
    "Me.Sm",
    "Me.Eu",
    "Me.Gd",
    "Me.Tb",
    "Me.Dy",
    "Me.Ho",
    "Me.Er",
    "Me.Tm",
    "Me.Yb",
    "Me.Lu",
    "Me.Y",
    "Me.Sc",
}


def number(value):
    try:
        result = Decimal(str(value).strip().replace(",", ""))
        return result if result.is_finite() else None
    except InvalidOperation:
        return None


def audit(root: Path, by_site):
    members = {}
    for site, rows in by_site.items():
        for row in rows:
            if row["source_id"] != "jasansky":
                continue
            prefix, facility = row["record_id"].split(":", 1)
            if prefix != "jasansky" or facility in members:
                raise ValueError("Ambiguous Jasansky facility membership")
            members[facility] = (site, row)
    paths = {(r["source_file"], r["raw_sha256"]) for _, r in members.values()}
    if len(paths) != 1:
        raise ValueError("One source-bound Jasansky snapshot required")
    relative, archive_hash = paths.pop()
    archive = root / "data/raw" / relative
    if digest(archive.read_bytes()) != archive_hash:
        raise ValueError("Changed Jasansky archive")
    with zipfile.ZipFile(archive) as source:
        reserves = source.read(RESERVES)
        citations = source.read(SOURCES)
    sources = {
        r["source_id"]: r for r in csv.DictReader(io.StringIO(citations.decode("utf-8-sig")))
    }
    attached = []
    for line, raw in enumerate(
        csv.DictReader(io.StringIO(reserves.decode("utf-8-sig"), newline="")), 2
    ):
        facility = raw["facility_id"]
        if facility not in members:
            continue
        site, row = members[facility]
        metal = raw["commodity"]
        ore = number(raw["mineral_value_tonnes"])
        grade = number(raw["grade_ppm"])
        year = number(raw["year"])
        if metal not in PILOT_METALS:
            reason = "different_commodity"
        elif ore is None or ore <= 0:
            reason = "missing_positive_ore_tonnage"
        elif grade is None or grade <= 0:
            reason = "missing_positive_grade"
        elif year is None or year != year.to_integral_value() or not 1900 <= year <= 2021:
            reason = "missing_historical_year"
        else:
            reason = "qualifying_historical_estimate"
        if raw["source_id"] not in sources:
            raise ValueError("Reserve row source is missing")
        ev = provenance(
            row,
            raw,
            "historical_facility_reserve",
            "The facility-specific reserve table records a historical tonnage and grade.",
        )
        ev.update(
            source_table=RESERVES,
            source_row=line,
            native_row_id=raw["id"],
            table_sha256=digest(reserves),
            year=raw["year"],
            units={
                "mineral_value_tonnes": "metric tonnes of material",
                "commodity_value_tonnes": "metric tonnes of contained commodity",
                "grade_ppm": "parts per million",
            },
            original_citation=sources[raw["source_id"]],
        )
        attached.append(
            {
                "site_id": site,
                "record_id": row["record_id"],
                "facility_id": facility,
                "eligibility": reason,
                "evidence": ev,
            }
        )
    return {
        "schema_version": "jasansky-reserve-audit-v1",
        "archive": relative,
        "archive_sha256": archive_hash,
        "reserves_csv_sha256": digest(reserves),
        "source_ids_csv_sha256": digest(citations),
        "eligible_facilities": len(members),
        "facilities_with_attached_rows": len({r["facility_id"] for r in attached}),
        "eligible_archive_rows": len(attached),
        "eligibility_counts": dict(sorted(Counter(r["eligibility"] for r in attached).items())),
        "rows": attached,
    }


def independent_rows(audit, reviewer):
    expected = [r for r in audit["rows"] if r["eligibility"] == "qualifying_historical_estimate"]
    if (
        reviewer.get("schema_version") != "independent-jasansky-reserve-review-v1"
        or not isinstance(reviewer.get("reviewer"), str)
        or not reviewer["reviewer"].strip()
        or not isinstance(reviewer.get("method"), str)
        or len(reviewer["method"].strip()) < 20
        or any(
            reviewer.get(k) != audit[k]
            for k in (
                "archive_sha256",
                "reserves_csv_sha256",
                "eligible_facilities",
                "eligible_archive_rows",
            )
        )
    ):
        raise ValueError("Independent reserve verification has different provenance or coverage")
    supplied = reviewer.get("qualifying_rows", [])
    indexed = {str(r["native_row_id"]): r for r in supplied}
    if len(indexed) != len(supplied) or set(indexed) != {
        r["evidence"]["native_row_id"] for r in expected
    }:
        raise ValueError("Independent reserve verification has different qualifying rows")
    for r in expected:
        ev = r["evidence"]
        f = ev["fields"]
        checked = indexed[ev["native_row_id"]]
        if (
            checked["facility_id"] != r["facility_id"]
            or checked["commodity"] != f["commodity"]
            or number(checked["physical_csv_row"]) != ev["source_row"]
            or number(checked["year"]) != number(f["year"])
            or number(checked["ore_tonnes"]) != number(f["mineral_value_tonnes"])
            or number(checked["grade_ppm"]) != number(f["grade_ppm"])
        ):
            raise ValueError("Independent reserve verification disagrees on a source value")
    return expected


def overlay(scored, audit, reviewer):
    confirmed = independent_rows(audit, reviewer)
    by_site = defaultdict(list)
    for entry in confirmed:
        by_site[entry["site_id"]].append(entry)
    corrected = copy.deepcopy(scored)
    changes = []
    for row in corrected:
        evidence = by_site.get(row["site_id"], [])
        if not evidence or row["maturity_rung"] >= 2:
            continue
        if any(e["record_id"] not in row["source_record_ids"] for e in evidence):
            raise ValueError("Reserve correction would cross a site boundary")
        evidence.sort(key=lambda e: (-int(e["evidence"]["year"]), e["evidence"]["source_row"]))
        change = {
            "site_id": row["site_id"],
            "original_rung": row["maturity_rung"],
            "reviewed_rung": 2,
            "source_record_ids": sorted({e["record_id"] for e in evidence}),
            "reason": (
                "The exact member facility has reported historical ore tonnage and pilot-metal "
                "grade in the Jasansky reserve table."
            ),
            "evidence": [e["evidence"] for e in evidence],
        }
        changes.append(change)
        row.update(
            maturity_rung=2,
            maturity_label="Historical or non-compliant estimate",
            rung_evidence=evidence[0]["evidence"],
            review_correction={
                "original_rung": change["original_rung"],
                "basis": "independent_source_correction",
                "native_row_ids": [e["evidence"]["native_row_id"] for e in evidence],
            },
            limitations=[
                (
                    "The dated estimate does not establish a current code-compliant resource or "
                    "reserve. Individual material rows remain separate."
                )
            ],
        )
    return corrected, changes
