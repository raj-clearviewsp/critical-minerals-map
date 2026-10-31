"""Explainable review routing; these fixed rules neither label nor merge records.

The accepted candidate universe is unchanged. Distances and typed source facts
rank review work, while uncertain scope, weak aliases and coincident points stay
visible. Numeric features describe text similarity, never a match probability.
"""

from __future__ import annotations

import math
import re
from collections import Counter, defaultdict
from difflib import SequenceMatcher

from pipeline.link.candidates import haversine_km, normalize_name

QUEUES = frozenset({"likely_match", "likely_distinct", "relationship", "ambiguous"})
ENTITY_LEVELS = frozenset({"site", "subsite", "operation_complex", "district_area", "unknown"})
BROAD_LEVELS = frozenset({"operation_complex", "district_area"})
SITE_LEVELS = frozenset({"site", "subsite"})
GENERIC_TOKENS = frozenset(
    {
        "mine",
        "mines",
        "mining",
        "deposit",
        "deposits",
        "prospect",
        "prospects",
        "project",
        "projects",
        "occurrence",
        "occurrences",
        "operation",
        "operations",
        "complex",
        "district",
        "area",
        "field",
        "fields",
        "plant",
        "plants",
        "refinery",
        "smelter",
        "shoot",
        "shoots",
        "shaft",
        "shafts",
        "pit",
        "pits",
        "ore",
        "ores",
        "no",
        "number",
        "the",
        "and",
        "of",
        "at",
        "nickel",
        "cobalt",
        "copper",
        "gold",
        "silver",
        "thorium",
        "uranium",
        "iron",
        "rare",
        "earth",
        "earths",
        "ree",
        "sulfide",
        "sulphide",
        "oxide",
        "laterite",
        "placer",
        "sand",
        "sands",
        "mineral",
        "minerals",
        "metal",
        "metals",
        "ni",
        "co",
        "cu",
        "au",
    }
)
DIRECTIONS = frozenset(
    {
        "north",
        "south",
        "east",
        "west",
        "northern",
        "southern",
        "eastern",
        "western",
        "northeast",
        "northwest",
        "southeast",
        "southwest",
    }
)
DIRECTION_FOLD = {"northern": "north", "southern": "south", "eastern": "east", "western": "west"}
PLACEHOLDER = re.compile(
    r"^(?:unknown(?: name)?|unnamed(?: mine| deposit| prospect| occurrence)?|"
    r"no (?:name|alternate name|data)|not (?:named|recorded|known|available|supplied)|"
    r"name (?:unknown|not available)|unidentified|unspecified|none|null|n a|na|n r|nr)[ .?\-]*$",
    re.I,
)


class TriageRuleError(ValueError):
    """An accepted candidate or enriched record is inconsistent."""


def _strings(value, field: str) -> list[str]:
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise TriageRuleError(f"{field} must be a list of strings")
    return value


def _variant(value: str | None) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        raise TriageRuleError("Names must be strings or null")
    raw = normalize_name(value)
    if not raw or PLACEHOLDER.fullmatch(raw) or not any(c.isalnum() for c in raw):
        return ""
    return raw


def _variants(values: list[str]) -> tuple[str, ...]:
    return tuple(sorted({normalized for value in values if (normalized := _variant(value))}))


def _tokens(name: str) -> frozenset[str]:
    return frozenset(token for token in name.split() if token not in GENERIC_TOKENS)


def _substantive(token: str) -> bool:
    return token not in DIRECTIONS and not any(c.isdigit() for c in token) and len(token) > 1


def _record(row: dict) -> dict:
    identity = row.get("record_id")
    if not isinstance(identity, str) or not identity:
        raise TriageRuleError("Enriched records require a nonblank record_id")
    for field, bound in (("lat", 90), ("lon", 180)):
        value = row.get(field)
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
        ):
            raise TriageRuleError("Triage requires finite located records")
        if not -bound <= value <= bound:
            raise TriageRuleError("Record coordinates are outside WGS84 bounds")
    if row["lat"] == row["lon"] == 0:
        raise TriageRuleError("Triage cannot treat a zero/zero sentinel as a location")
    if row.get("entity_level") not in ENTITY_LEVELS:
        raise TriageRuleError("Unknown entity_level")
    source = row.get("source_id")
    if not isinstance(source, str) or not source:
        raise TriageRuleError("Enriched records require a source_id")
    aliases = _strings(row.get("identity_aliases", []), "identity_aliases")
    primary = row.get("primary_name")
    # A source-specific enrichment flag can be more conservative than generic
    # placeholder recognition; it cannot make a placeholder informative.
    primary = primary if row.get("primary_name_usable", True) else None
    trusted = _variants([name for name in [primary, *aliases] if name is not None])
    primary_normalized = _variant(primary)
    return {
        "record_id": identity,
        "source_id": source,
        "lat": float(row["lat"]),
        "lon": float(row["lon"]),
        "level": row["entity_level"],
        "names": trusted,
        "tokens": tuple(_tokens(name) for name in trusted),
        "primary": primary_normalized,
        "context": _variants(_strings(row.get("context_names", []), "context_names")),
        "unverified": _variants(_strings(row.get("unverified_aliases", []), "unverified_aliases")),
        "flags": sorted(set(_strings(row.get("scope_flags", []), "scope_flags"))),
        "types": sorted(set(_strings(row.get("deposit_types", []), "deposit_types")) - {"unknown"}),
        "countries": sorted(set(_strings(row.get("country_iso3s", []), "country_iso3s"))),
        "families": sorted(set(_strings(row.get("pilot_families", []), "pilot_families"))),
    }


def _coord(record: dict) -> tuple[float, float]:
    # Millimetre-scale floating-point differences between source views should
    # not conceal publisher coordinate reuse. This is not a location precision.
    return round(record["lat"], 8), round(record["lon"], 8)


def _name_features(left: dict, right: dict, frequencies: Counter, total: int) -> dict:
    exact = sorted(set(left["names"]) & set(right["names"]))
    best_overlap, best_tokens = 0.0, frozenset()
    for a in left["tokens"]:
        for b in right["tokens"]:
            common, union = a & b, a | b
            weights = {
                token: math.log((total + 1) / (frequencies[token] + 1)) + 1
                for token in sorted(union)
            }
            denominator = sum(weights.values())
            overlap = (
                sum(weights[token] for token in sorted(common)) / denominator if denominator else 0
            )
            if (overlap, len(common), tuple(sorted(common))) > (
                best_overlap,
                len(best_tokens),
                tuple(sorted(best_tokens)),
            ):
                best_overlap, best_tokens = overlap, common
    rare_limit = max(5, math.ceil(total * 0.01))
    distinctive = sorted(
        token for token in best_tokens if _substantive(token) and frequencies[token] <= rare_limit
    )
    meaningful_variants = [
        [
            " ".join(token for token in name.split() if token not in GENERIC_TOKENS)
            for name in record["names"]
        ]
        for record in (left, right)
    ]
    character = max(
        (
            SequenceMatcher(None, a, b, autojunk=False).ratio()
            for a in meaningful_variants[0]
            for b in meaningful_variants[1]
            if min(len(a), len(b)) >= 6
        ),
        default=0.0,
    )
    numbers = [set(re.findall(r"\d+[a-z]?", record["primary"])) for record in (left, right)]
    directions = [
        {
            DIRECTION_FOLD.get(token, token)
            for token in record["primary"].split()
            if token in DIRECTIONS
        }
        for record in (left, right)
    ]
    opposing = (
        {"north", "south"},
        {"east", "west"},
        {"northeast", "southwest"},
        {"northwest", "southeast"},
    )
    directional_conflict = any(
        len(pair & directions[0]) == len(pair & directions[1]) == 1
        and (pair & directions[0]) != (pair & directions[1])
        for pair in opposing
    )
    context_matches = sorted(
        (set(left["context"]) & set((*right["names"], *right["context"], *right["unverified"])))
        | (set(right["context"]) & set((*left["names"], *left["unverified"])))
    )
    unverified_matches = sorted(
        (set(left["unverified"]) & set((*right["names"], *right["unverified"])))
        | (set(right["unverified"]) & set(left["names"]))
    )
    return {
        "trusted_exact_name": bool(exact),
        "trusted_exact_variants": exact,
        "shared_name_tokens": sorted(best_tokens),
        "distinctive_shared_tokens": distinctive,
        "shared_token_record_frequencies": {
            token: frequencies[token] for token in sorted(best_tokens)
        },
        "weighted_name_overlap": round(best_overlap, 6),
        "max_character_similarity": round(character, 6),
        "context_exact_names": context_matches,
        "unverified_exact_names": unverified_matches,
        "left_number_tokens": sorted(numbers[0]),
        "right_number_tokens": sorted(numbers[1]),
        "numeric_conflict": bool(numbers[0] and numbers[1] and numbers[0] != numbers[1]),
        "left_direction_tokens": sorted(directions[0]),
        "right_direction_tokens": sorted(directions[1]),
        "directional_conflict": directional_conflict,
        "direction_qualifier_difference": directions[0] != directions[1],
        "meaningful_name_missing": not any(left["tokens"]) or not any(right["tokens"]),
    }


def _band(distance: float) -> str:
    for bound, label in (
        (0.25, "within_250m"),
        (1, "over_250m_to_1km"),
        (2, "over_1_to_2km"),
        (5, "over_2_to_5km"),
    ):
        if distance <= bound:
            return label
    return "over_5km"


def _route(
    left: dict, right: dict, features: dict, mirror: dict | None
) -> tuple[str, str, list[str], str]:
    if mirror is not None:
        return (
            "likely_match",
            "source_view_duplicate",
            ["verified_publisher_source_views"],
            "The publisher workbook and service describe the same source record. Reconcile the "
            "two source views while retaining both provenance trails; this is not independent "
            "corroboration or permission to merge other sites.",
        )
    if left["level"] in BROAD_LEVELS or right["level"] in BROAD_LEVELS:
        return (
            "relationship",
            "relationship_scope_review",
            ["broader_than_individual_site"],
            "At least one source describes an area, district, operation or complex. Review the "
            "scope and any containment relationship before considering individual-site identity. "
            "A relationship or its direction has not been asserted.",
        )
    if features["numeric_conflict"] or features["directional_conflict"]:
        reasons = []
        if features["numeric_conflict"]:
            reasons.append("different_numbered_identifiers")
        if features["directional_conflict"]:
            reasons.append("opposing_directional_identifiers")
        return (
            "ambiguous",
            "identifier_conflict_review",
            reasons,
            "The source names contain different numbered or directional identifiers. These "
            "can distinguish neighboring deposits or subsites; proximity and generic name "
            "overlap cannot resolve the conflict.",
        )
    # Ordinary missing scope or names is neutral evidence and may still route
    # to low-priority auditing. Positive indications of group/processing scope
    # need clarification before any individual-site identity assessment.
    critical_flags = sorted(
        set(left["flags"] + right["flags"])
        - {
            "entity_level_unresolved",
            "placeholder_primary_name",
        }
    )
    if critical_flags:
        return (
            "ambiguous",
            "scope_review",
            ["source_scope_requires_review"],
            "Source scope needs clarification before identity matching: "
            + "; ".join(critical_flags)
            + ".",
        )
    compatible_level = left["level"] in SITE_LEVELS and right["level"] in SITE_LEVELS
    if (
        features["distance_km"] <= 0.25
        and features["trusted_exact_name"]
        and features["distinctive_shared_tokens"]
        and compatible_level
        and not left["flags"]
        and not right["flags"]
        and not features["direction_qualifier_difference"]
        and not features["country_conflict"]
        and not features["coordinate_reuse"]
        and features["deposit_type_comparison"] != "different_reported_codes"
    ):
        return (
            "likely_match",
            "audit_likely_match",
            ["close_distinctive_name_agreement"],
            "Within 250 metres, with an exact meaningful primary name or trusted alias and "
            "compatible individual-site scope. No checked scope, identifier, country or "
            "reported-type conflict was found. This is a review priority, not a validated match.",
        )
    if (
        features["distance_km"] > 2
        and not features["shared_name_tokens"]
        and not features["unverified_exact_names"]
        and not features["context_exact_names"]
        and features["max_character_similarity"] < 0.82
    ):
        scope_note = "" if compatible_level else " Individual-site scope also remains unconfirmed."
        reasons = ["distant_without_meaningful_name_agreement"]
        if not compatible_level:
            reasons.append("individual_site_scope_unconfirmed")
        return (
            "likely_distinct",
            "audit_likely_distinct",
            reasons,
            "Over 2 kilometres apart without meaningful shared name tokens, a matching "
            "reported alias, or a close text variant. Deprioritize for manual review and audit "
            "a sample; this does not establish that the records are different sites." + scope_note,
        )
    reasons = []
    messages = []
    if features["context_exact_names"] or features["unverified_exact_names"]:
        reasons.append("weak_alias_or_context_agreement")
        messages.append("The matching name occurs in contextual or unverified aliases")
    if features["coordinate_reuse"]:
        reasons.append("coordinates_reused_for_different_names")
        messages.append("At least one coordinate is reused for differently named records")
    if not compatible_level:
        reasons.append("individual_site_scope_unconfirmed")
        messages.append("Individual-site scope is not confirmed on both sides")
    if features["country_conflict"]:
        reasons.append("reported_country_disagreement")
        messages.append("The reported host countries disagree")
    if features["deposit_type_comparison"] == "different_reported_codes":
        reasons.append("reported_deposit_types_differ")
        messages.append("The sources report different geological type codes")
    if features["direction_qualifier_difference"]:
        reasons.append("directional_qualifier_difference")
        messages.append("Only one name carries a directional qualifier, or the qualifiers differ")
    if not reasons:
        reasons.append("insufficient_joint_identity_evidence")
        messages.append(
            "Distance and available name/classification evidence do not give a clear route"
        )
    return (
        "ambiguous",
        "identity_review",
        reasons,
        ". ".join(messages) + ". Review the source evidence.",
    )


def triage_pairs(
    candidates: list[dict], records: dict[str, dict], source_views: dict
) -> list[dict]:
    """Return one explainable queue assignment for each unchanged candidate pair.

    Thresholds are fixed preparation rules, not fitted parameters. Human decisions
    are deliberately not accepted as inputs. Every output forbids auto-merging;
    likely queues still require an independent audit before any model acceptance.
    """
    prepared = {identity: _record(row) for identity, row in records.items()}
    if any(identity != row["record_id"] for identity, row in prepared.items()):
        raise TriageRuleError("Record lookup key differs from record_id")
    frequencies = Counter()
    coordinate_records, coordinate_names = defaultdict(set), defaultdict(set)
    for record in prepared.values():
        frequencies.update(set().union(*record["tokens"]) if record["tokens"] else set())
        coordinate_records[_coord(record)].add(record["record_id"])
        if record["primary"]:
            coordinate_names[_coord(record)].add(record["primary"])
    mirrors = {}
    for view in source_views.get("pairs", []):
        identity = tuple(sorted((view["left_record_id"], view["right_record_id"])))
        if identity[0] == identity[1] or any(record_id not in prepared for record_id in identity):
            raise TriageRuleError("Invalid source-view reconciliation pair")
        if identity in mirrors:
            raise TriageRuleError("Duplicate source-view reconciliation pair")
        mirrors[identity] = view
    seen_ids, seen_pairs, output = set(), set(), []
    for candidate in sorted(candidates, key=lambda pair: pair["pair_id"]):
        if any(field in candidate for field in ("label", "reviewer", "review_notes")):
            raise TriageRuleError("Candidate inputs must not contain human review decisions")
        pair_id = candidate["pair_id"]
        ids = candidate["left_record_id"], candidate["right_record_id"]
        identity = tuple(sorted(ids))
        if pair_id in seen_ids or identity in seen_pairs or ids[0] == ids[1]:
            raise TriageRuleError("Repeated candidate identity or self-pair")
        if any(record_id not in prepared for record_id in ids):
            raise TriageRuleError("Candidate references an unknown enriched record")
        seen_ids.add(pair_id)
        seen_pairs.add(identity)
        left, right = (prepared[record_id] for record_id in ids)
        distance = candidate.get("distance_km")
        if (
            isinstance(distance, bool)
            or not isinstance(distance, (int, float))
            or not math.isfinite(distance)
        ):
            raise TriageRuleError("Candidate distance must be finite")
        actual_distance = haversine_km(left["lat"], left["lon"], right["lat"], right["lon"])
        if (
            distance < 0
            or distance > 25
            or not math.isclose(distance, actual_distance, abs_tol=1e-7)
        ):
            raise TriageRuleError("Candidate distance differs from source coordinates")
        if set(candidate["shared_families"]) != set(left["families"]) & set(right["families"]):
            raise TriageRuleError("Candidate shared families differ from source evidence")
        if not candidate["shared_families"]:
            raise TriageRuleError("Candidate requires a shared pilot family")
        mirror = mirrors.get(identity)
        types = "missing_on_one_or_both"
        if left["types"] and right["types"]:
            types = (
                "shared_reported_code"
                if set(left["types"]) & set(right["types"])
                else "different_reported_codes"
            )
        features = {
            **_name_features(left, right, frequencies, len(prepared)),
            "distance_km": distance,
            "distance_band": _band(distance),
            "left_entity_level": left["level"],
            "right_entity_level": right["level"],
            "left_deposit_types": left["types"],
            "right_deposit_types": right["types"],
            "deposit_type_comparison": types,
            "country_conflict": bool(
                left["countries"]
                and right["countries"]
                and not set(left["countries"]) & set(right["countries"])
            ),
            "left_coordinate_count": len(coordinate_records[_coord(left)]),
            "right_coordinate_count": len(coordinate_records[_coord(right)]),
            "left_coordinate_name_count": len(coordinate_names[_coord(left)]),
            "right_coordinate_name_count": len(coordinate_names[_coord(right)]),
            "coordinate_reuse": (
                len(coordinate_names[_coord(left)]) > 1 or len(coordinate_names[_coord(right)]) > 1
            ),
            "source_view_duplicate": mirror is not None,
            "source_view_evidence": mirror.get("evidence") if mirror else None,
            "same_source": left["source_id"] == right["source_id"],
            "left_scope_flags": left["flags"],
            "right_scope_flags": right["flags"],
        }
        queue, disposition, reasons, explanation = _route(left, right, features, mirror)
        output.append(
            {
                **candidate,
                "queue": queue,
                "disposition": disposition,
                "reason_codes": reasons,
                "explanation": explanation,
                "features": features,
                "requires_adjudication": queue in {"relationship", "ambiguous"},
                "auto_merge_eligible": False,
                "rule_policy": (
                    "Fixed review triage v2; no fitted model, probability, human label or merge"
                ),
            }
        )
    return output
