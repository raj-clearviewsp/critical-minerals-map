"""Source-supported geological commodity associations, without entity inference.

Source-scope fallbacks have narrow documentary bases:
* USGS 10.5066/F7DR2TN4 describes Global_REE and its nonspatial companion as
  rare-earth deposits and occurrences. Scope is used only for a blank Commods cell.
* USGS OFR 2011-1058 metadata explicitly describes its purpose as information for
  nickel deposits. This supports Ni when individual grade fields are unavailable;
  the report's Ni-Co title alone does not establish cobalt at every deposit.

MRDS About.txt defines Commodity rows as commodities present at a site and its
importance as primary, secondary or tertiary. Those roles are preserved; none
asserts that a reported associated commodity was recovered as a byproduct.
"""

from __future__ import annotations

import json
import math
import re

from pipeline.vocab.core import Vocabulary

SOURCES = {
    "usgs_global_ree",
    "usgs_global_critical",
    "usgs_us_critical",
    "usgs_laterites",
    "usgs_mrds",
}
SOURCE_FIELDS = {
    "usgs_global_ree": "Commods",
    "usgs_global_critical": "CRITICAL_MINERAL",
    "usgs_us_critical": "critmin",
}
MISSING_GRADES = {"n.d.", "n.d", "nd", "n/a", "na", "unknown", "not determined", "-", "—"}
QUALIFIER = re.compile(r"[?<>≤≥~≈]|\b(?:uncertain|possible|possibly|probable|inferred)\b", re.I)


def _object(row: dict, column: str) -> dict:
    value = row.get(column, {})
    if isinstance(value, str):
        value = json.loads(value)
    if not isinstance(value, dict):
        raise ValueError(f"Expected a JSON object in {column}")
    return value


def _text(value) -> str | None:
    if value is None:
        return None
    if isinstance(value, (dict, list, bool)):
        raise ValueError("Expected a scalar commodity value")
    raw = str(value)
    return raw if raw.strip() else None


def _evidence(field, raw, codes, *, role="unspecified", basis="source_term", unmapped=()):
    return {
        "field": field,
        "raw_value": raw,
        "codes": sorted(set(codes)),
        "role": role,
        "basis": basis,
        "qualified": bool(QUALIFIER.search(raw)),
        "unmapped": list(unmapped),
    }


def _term(vocab: Vocabulary, source: str, field: str, value, *, role="unspecified"):
    raw = _text(value)
    if raw is None:
        return None
    resolved = vocab.resolve("commodity", source, field, raw)
    return _evidence(field, raw, resolved["codes"], role=role, unmapped=resolved["unmapped"])


def _scope(vocab: Vocabulary, source: str, term: str, raw: str) -> dict:
    code = vocab.lookup("commodities", source, term)
    if code is None:
        raise ValueError(f"Missing reviewed source-scope commodity: {source}/{term}")
    return _evidence("source_scope", raw, [code], basis="source_scope")


def _deduplicate(evidence: list[dict]) -> list[dict]:
    """Keep the first pointer for repeated claims while preserving distinct roles."""
    unique = {}
    for item in evidence:
        identity = (item["field"], item["raw_value"], item["role"])
        unique.setdefault(identity, item)
    return list(unique.values())


def _pointer(*parts) -> str:
    return "/" + "/".join(str(part).replace("~", "~0").replace("/", "~1") for part in parts)


def _laterites(commodities: dict, vocab: Vocabulary) -> list[dict]:
    evidence = []
    policies = vocab.rules["commodity_fields"]["usgs_laterites"]
    for field, value in commodities.items():
        raw = _text(value)
        if raw is None or raw.strip().casefold() in MISSING_GRADES:
            continue
        if field not in policies:
            # A new source column needs review, never a substring guess at Ni/Co.
            evidence.append(_evidence(field, raw, [], basis="column_header", unmapped=[field]))
            continue
        try:
            numeric = float(raw.strip().removesuffix("%").strip())
        except ValueError:
            numeric = None
        if numeric is not None and math.isfinite(numeric) and numeric == 0:
            continue
        if numeric is None or not math.isfinite(numeric) or numeric < 0:
            # E.g. '<0.1' can describe a nondetection: an upper bound is not
            # sufficient evidence of cobalt presence. Preserve it for review.
            evidence.append(_evidence(field, raw, [], basis="column_header", unmapped=[raw]))
            continue
        resolved = vocab.resolve("commodity", "usgs_laterites", field, field)
        evidence.append(
            _evidence(
                field,
                raw,
                resolved["codes"],
                basis="column_header",
                unmapped=resolved["unmapped"],
            )
        )
    if not any("Ni" in item["codes"] for item in evidence):
        evidence.append(
            _scope(
                vocab,
                "usgs_laterites",
                "nickel",
                "Nickel Laterite Deposits of the World: Database and Grade and Tonnage Models",
            )
        )
    return evidence


def _mrds(row: dict, commodities: dict, vocab: Vocabulary) -> list[dict]:
    evidence = []
    item = _term(vocab, "usgs_mrds", "code_list", commodities.get("code_list"))
    if item:
        item["source_path"] = _pointer("raw_commodities", "code_list")
        evidence.append(item)
    children = commodities.get("Commodity.txt")
    child_path = ("raw_commodities", "Commodity.txt")
    child_suffix = ()
    if children is None:
        raw_fields = _object(row, "raw_fields")
        linked = raw_fields.get("linked_tables", {}).get("Commodity.txt", [])
        children = [child["values"] for child in linked]
        child_path = ("raw_fields", "linked_tables", "Commodity.txt")
        child_suffix = ("values",)
    if not isinstance(children, list):
        raise ValueError("Expected MRDS Commodity.txt child records")
    for index, child in enumerate(children):
        if not isinstance(child, dict):
            raise ValueError("Expected MRDS commodity child object")
        role = {
            "Primary": "primary",
            "Secondary": "secondary",
            "Tertiary": "tertiary",
        }.get(child.get("import"), "unspecified")
        for field in ("code", "commod"):
            item = _term(vocab, "usgs_mrds", field, child.get(field), role=role)
            if item:
                item["source_path"] = _pointer(*child_path, index, *child_suffix, field)
                evidence.append(item)
    return _deduplicate(evidence)


def commodity_evidence(source_id: str, row: dict, vocab: Vocabulary) -> list[dict]:
    """Return associations from this single staged record, including unmapped text.

    JSON columns may be staged strings or already-decoded objects. Codes include
    nonpilot commodities; callers select with ``vocab.pilot_codes``. No role is
    inferred from list position, grade magnitude, producer status, or mineralogy.
    """
    if source_id not in SOURCES:
        raise ValueError(f"Unsupported geological source: {source_id}")
    if row.get("source_id", source_id) != source_id:
        raise ValueError("Record source_id does not match the geological adapter")
    commodities = _object(row, "raw_commodities")
    if source_id == "usgs_mrds":
        return _mrds(row, commodities, vocab)
    if source_id == "usgs_laterites":
        return _laterites(commodities, vocab)
    field = SOURCE_FIELDS[source_id]
    item = _term(vocab, source_id, field, commodities.get(field))
    if item:
        return [item]
    if source_id == "usgs_global_ree":
        return [_scope(vocab, source_id, "REE", "Global rare earth element occurrence database")]
    return []
