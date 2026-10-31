"""Source-aware commodity evidence from the ten tabular staging sources.

Evidence identifies terminology and source roles, never current production,
quantities, ownership, or maturity. This module does not decide record retention.
"""

from __future__ import annotations

import json
import re
from functools import lru_cache

from pipeline.vocab.core import Vocabulary

SOURCE_IDS = (
    "gseu_crm",
    "ga_global_ree",
    "ga_critical_2025",
    "ga_critical_2025_arcgis",
    "icmm",
    "wapha",
    "jasansky",
    "usgs_mcs_2026",
    "oecd_export",
    "aiddata_transition_v2",
)

QUALIFIER = re.compile(r"\?|±|\+\s*/\s*-|\b(?:possible|potential|probable|uncertain)\b", re.I)
ROLES = {
    "icmm": {"Primary Commodity": "primary", "Secondary Commodity ": "secondary"},
    "jasansky": {"primary_commodity": "primary"},
    "aiddata_transition_v2": {
        "Commodity": "collateral",
        "Primary_Minerals": "primary",
        "Subsidiary_Minerals": "secondary",
    },
}
# These are linked observations for the same facility, not dictionary or ownership
# rows. Input/output/material labels are unspecified associations, not byproducts.
# Waste mineral content is excluded from retention evidence; source waste rows
# remain preserved in staging and do not establish an extracted commodity.
JASANSKY_FIELDS = {
    "data/capacity.csv": ("material", "commodity"),
    "data/coal.csv": ("material",),
    "data/commodities.csv": ("material", "commodity"),
    "data/minerals.csv": ("material",),
    "data/processing.csv": ("input", "output"),
    "data/reserves.csv": ("material", "commodity"),
    "data/transport.csv": ("product",),
}


def _object(row: dict, field: str) -> dict:
    value = row.get(field, {})
    if isinstance(value, str):
        value = json.loads(value)
    if not isinstance(value, dict):
        raise ValueError(f"Staging {field} must contain a JSON object")
    return value


def _text(value) -> str | None:
    if value is None:
        return None
    if isinstance(value, (dict, list)):
        raise ValueError("A commodity source cell must be a scalar")
    raw = str(value)
    return raw if raw.strip() else None


@lru_cache(maxsize=16384)
def _resolve(vocab: Vocabulary, source: str, field: str, raw: str) -> tuple[tuple, tuple]:
    """Cache repeated cells, not records; never weaken exact source-field policies."""
    policies = vocab.rules["commodity_fields"].get(source, {})
    if source == "ga_global_ree" and field == "source_scope":
        # The Step 4 parser records the publisher's documented compilation scope.
        # There is no commodity cell: Element is tectonic geography, not an element.
        code = vocab.lookup("commodities", source, raw) if raw == "REE" else None
        return ((code,), ()) if code else ((), (raw,))
    policy = policies.get(field)
    if policy is None:
        return (), (raw,)
    if source == "oecd_export":
        # Positive HS identities were reviewed against CL_PRODUCT_HS2007. Do not
        # resolve arbitrary labels, exclusions, or policy-description keywords.
        codes = policy.get("values", {}).get(raw)
        return (tuple(codes), ()) if codes is not None else ((), (raw,))
    result = vocab.resolve("commodity", source, field, raw)
    return tuple(result["codes"]), tuple(result["unmapped"])


def _evidence(source, field, value, vocab, *, source_path=None):
    raw = _text(value)
    if raw is None:
        return None
    codes, unknown = _resolve(vocab, source, field, raw)
    evidence = {
        "field": field,
        "raw_value": raw,
        "codes": sorted(set(codes)),
        "role": ROLES.get(source, {}).get(field, "unspecified"),
        "basis": "source_scope"
        if source == "ga_global_ree" and field == "source_scope"
        else "source_term",
        "qualified": bool(QUALIFIER.search(raw)),
        "unmapped": list(unknown),
    }
    if source_path is not None:
        evidence["source_path"] = source_path
    return evidence


def _pointer(value: str) -> str:
    return value.replace("~", "~0").replace("/", "~1")


def commodity_evidence(source_id: str, row: dict, vocab: Vocabulary) -> list[dict]:
    """Return one evidence entry per nonblank source cell, including unmapped terms.

    ``row`` is a normalized Parquet row whose JSON columns are strings. Exact
    original field names and whole cells remain intact. Optional ``source_path``
    is a JSON pointer to linked Jasansky evidence within the decoded staging row;
    its enclosing object retains the source table, physical row number and file.
    Qualified wording is flagged, never stripped to invent a new alias.
    """
    if source_id not in SOURCE_IDS:
        raise ValueError(f"Unsupported tabular source: {source_id}")
    evidence = []
    for field, value in sorted(_object(row, "raw_commodities").items()):
        item = _evidence(source_id, field, value, vocab)
        if item is not None:
            evidence.append(item)
    if source_id != "jasansky":
        return evidence
    fields = _object(row, "raw_fields")
    linked = fields.get("_linked_tables", {})
    if not isinstance(linked, dict):
        raise ValueError("Jasansky linked evidence must be a table dictionary")
    for table in sorted(linked):
        if table not in JASANSKY_FIELDS:
            continue
        children = linked[table]
        if not isinstance(children, list):
            raise ValueError("Jasansky linked table must contain a row list")
        for index, child in enumerate(children):
            original = child.get("raw_fields")
            if not isinstance(original, dict):
                raise ValueError("Jasansky linked row must retain raw fields")
            if (
                not fields.get("facility_id")
                or original.get("facility_id") != fields["facility_id"]
            ):
                raise ValueError(
                    "Jasansky commodity evidence belongs to another or unknown facility"
                )
            for field in JASANSKY_FIELDS[table]:
                if field not in original:
                    continue
                table_path = f"/raw_fields/_linked_tables/{_pointer(table)}/{index}"
                path = f"{table_path}/raw_fields/{_pointer(field)}"
                item = _evidence(source_id, field, original[field], vocab, source_path=path)
                if item is not None:
                    evidence.append(item)
    return evidence
