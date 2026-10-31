"""Exact country evidence for pilot records, without geocoding or role inference.

``codes`` and top-level ``unresolved`` describe host/activity geography only.
Finance counterparties remain separate, labelled evidence; a financier's origin
never assigns an asset or a loan to that country. Conflicting host fields retain
their individual evidence but produce no country assignment.
"""

from __future__ import annotations

import json
from functools import lru_cache

import pycountry

from pipeline.vocab.core import Vocabulary

# Exact source fields, not a search through arbitrary prose or company names.
HOST_FIELDS = {
    "usgs_global_ree": (("Country", False),),
    "usgs_global_critical": (("LOCATION", False),),
    "usgs_laterites": (("Country", False),),
    "gseu_crm": (("ISO3", True), ("Country", False)),
    "ga_global_ree": (("Country", False),),
    "icmm": (("Country", False),),
    "jasansky": (("GID_0", True), ("country", False)),
    "usgs_mcs_2026": (("Country", False),),
    "oecd_export": (("REF_AREA", True),),
}

AIDDATA_HOST_FIELDS = (
    ("Country_of_Activity_ISO3", True),
    ("Country_of_Activity", False),
    ("Host_Country", False),
)
AIDDATA_ASSOCIATIONS = (
    ("recipient_incorporation", "DRA_Country_of_Inc_ISO3", True),
    ("recipient_incorporation", "DRA_Country_of_Inc", False),
    ("financier_origin", "Financier_Country", False),
    ("overseas_lender_jurisdiction", "Overseas_Jurisdiction_ISO3", True),
    ("overseas_lender_jurisdiction", "Overseas_Jurisdiction", False),
    ("organization_origin", "Organization_Origin", False),
    ("equity_holder_origin", "Equity_Holder_Origin", False),
)

US_STATES = frozenset(
    "AL AK AZ AR CA CO CT DE FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN MS MO "
    "MT NE NV NH NJ NM NY NC ND OH OK OR PA RI SC SD TN TX UT VT VA WA WV WI WY DC".split()
)
AU_STATES = frozenset("ACT NSW NT QLD SA TAS VIC WA".split())
JURISDICTION_BASIS = {
    "usgs_us_critical": (
        "USGS Critical mineral deposits of the United States; native state field. "
        "The 50 states/DC map to USA; explicit PR/Puerto Rico maps to ISO PRI. "
        "No location is inferred from coordinates."
    ),
    "ga_critical_2025": (
        "Geoscience Australia Australian Critical Minerals Map 2025; native State field "
        "identifies an Australian state/territory. No coordinates are used."
    ),
    "ga_critical_2025_arcgis": (
        "Geoscience Australia Australian Critical Minerals Operating Mines And Deposits; "
        "native state field identifies an Australian state/territory. No coordinates are used."
    ),
}


def _text(value) -> str | None:
    if value is None:
        return None
    text = str(value)
    return text if text.strip() else None


def _fields(row: dict) -> dict:
    value = row.get("raw_fields")
    if isinstance(value, str):
        value = json.loads(value)
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError("raw_fields must be a JSON object")
    return value


@lru_cache(maxsize=1024)
def _iso3(value: str) -> str | None:
    country = pycountry.countries.get(alpha_3=value.strip().upper())
    return country.alpha_3 if country else None


def _evidence(source, field, raw, role, iso3, vocab, *, multi=False, location="raw_fields"):
    parts = raw.split("|") if multi else [raw]
    codes, unresolved, reasons = set(), [], []
    for part in parts:
        if not part.strip():
            continue
        if iso3:
            code = _iso3(part)
            result = {
                "codes": [code] if code else [],
                "unmapped": [] if code else [part],
                "reason": "Exact ISO 3166-1 alpha-3" if code else "Not an assigned ISO alpha-3",
            }
        else:
            result = vocab.country(source, field, part)
        codes.update(result["codes"])
        unresolved.extend(result["unmapped"])
        reasons.append(result["reason"])
    return {
        "field": field,
        "location": location,
        "raw_value": raw,
        "role": role,
        "codes": sorted(codes),
        "unresolved": list(dict.fromkeys(unresolved)),
        "basis": "; ".join(dict.fromkeys(reasons)),
        "assigns_country": role == "host_activity",
        "preferred": iso3,
    }


def _jurisdiction(source: str, fields: dict) -> dict | None:
    field = "State" if source == "ga_critical_2025" else "state"
    raw = _text(fields.get(field))
    if raw is None:
        return None
    code = None
    if source == "usgs_us_critical":
        # This exact two-state value occurs once in the source. Do not generalize
        # splitting to geographic text or infer a country for an unknown state.
        if raw.strip().upper() in US_STATES or raw == "UT\nNV":
            code = "USA"
        elif raw.strip().upper() in {"PR", "PUERTO RICO"}:
            code = "PRI"
    elif raw.strip().upper() in AU_STATES:
        code = "AUS"
    return {
        "field": field,
        "location": "raw_fields",
        "raw_value": raw,
        "role": "host_activity",
        "codes": [code] if code else [],
        "unresolved": [] if code else [raw],
        "basis": JURISDICTION_BASIS[source],
        "assigns_country": True,
        "preferred": False,
    }


def _fips_evidence(fields: dict) -> list[dict]:
    values = []
    if _text(fields.get("fips")):
        values.append(("fips", fields["fips"]))
    for item in fields.get("linked_tables", {}).get("Place.txt", []):
        raw = item.get("values", {})
        if _text(raw.get("fips")):
            field = f"linked_tables.Place.txt[row={item.get('row_number')}].fips"
            values.append((field, raw["fips"]))
    return [
        {
            "field": field,
            "location": "raw_fields",
            "raw_value": str(value),
            "role": "host_activity",
            "codes": [],
            "unresolved": [str(value)],
            "basis": "MRDS Place FIPS identifier is not an ISO country code; no lookup supplied",
            "assigns_country": True,
            "preferred": False,
        }
        for field, value in values
    ]


def country_evidence(source_id: str, row: dict, vocab: Vocabulary) -> dict:
    """Resolve only explicit host country fields and documented source jurisdictions.

    Raw JSON fields are accepted as stored in staging Parquet or as decoded dicts.
    ISO3/GID_0 fields take precedence over an unrecognized country spelling, but
    invalid explicit codes and disagreeing valid fields prevent assignment.
    Only AidData's explicitly named country fields permit pipe-separated lists.
    A region, border description, FIPS identifier, or absent country stays unresolved.
    """
    fields = _fields(row)
    evidence, notes = [], []
    finance = source_id == "aiddata_transition_v2"
    host_fields = (
        AIDDATA_HOST_FIELDS
        if finance
        else HOST_FIELDS.get(
            source_id, (("ISO3", True), ("GID_0", True), ("Country", False), ("country", False))
        )
    )
    for field, iso3 in host_fields:
        if (raw := _text(fields.get(field))) is not None:
            evidence.append(
                _evidence(source_id, field, raw, "host_activity", iso3, vocab, multi=finance)
            )
    if source_id in JURISDICTION_BASIS:
        if item := _jurisdiction(source_id, fields):
            evidence.append(item)
            notes.append(JURISDICTION_BASIS[source_id])
    if source_id == "usgs_mrds":
        evidence.extend(_fips_evidence(fields))
        notes.append(
            "No MRDS FIPS-to-ISO lookup is present; source Place identifiers are retained."
        )

    # The normalized country usually echoes one native name field. Check an
    # independently different value instead of silently trusting either copy.
    country = _text(row.get("country"))
    if country and country not in {item["raw_value"] for item in evidence}:
        evidence.append(
            _evidence(
                source_id,
                "country",
                country,
                "host_activity",
                False,
                vocab,
                multi=finance,
                location="normalized",
            )
        )
    host = list(evidence)
    if finance:
        for role, field, iso3 in AIDDATA_ASSOCIATIONS:
            if (raw := _text(fields.get(field))) is not None:
                evidence.append(_evidence(source_id, field, raw, role, iso3, vocab, multi=True))
        notes.append(
            "AidData host/activity fields assign countries. Recipient incorporation, financier, "
            "overseas lender, organization and equity-holder origins are association evidence. "
            "These roles do not establish a mine location or foreign-state control."
        )
    unresolved = [value for item in host for value in item["unresolved"]]
    code_sets = {tuple(item["codes"]) for item in host if item["codes"]}
    conflict = len(code_sets) > 1
    invalid_preferred = any(item["preferred"] and item["unresolved"] for item in host)
    if conflict:
        for item in host:
            item["conflict"] = True
        unresolved.extend(item["raw_value"] for item in host)
        notes.append("Conflicting host/activity country evidence; no country assignment made.")
    if invalid_preferred:
        notes.append(
            "An explicit ISO3/GID_0/REF_AREA value is unresolved; no fallback assignment made."
        )
    codes = (
        [] if conflict or invalid_preferred else sorted({c for item in host for c in item["codes"]})
    )
    if not host:
        unresolved.append("Country not supplied")
        notes.append(
            "No explicit host country or supported source jurisdiction; no geocoding used."
        )
    return {
        "codes": codes,
        "evidence": evidence,
        "unresolved": list(dict.fromkeys(unresolved)),
        "notes": notes,
    }
