"""Spatial candidates and an unlabelled, stratified human-review sample.

Name categories are sampling descriptors, not match scores or predictions. This
module does not train a model, merge records, or assign site identifiers.
"""

from __future__ import annotations

import hashlib
import itertools
import json
import math
import unicodedata
from collections import Counter, defaultdict

EARTH_RADIUS_KM = 6371.0088
MAX_RADIUS_KM = 25.0
PILOT_FAMILIES = frozenset({"REE", "Ni", "Co"})
GENERIC_NAME_WORDS = frozenset({"mine", "mines", "deposit", "deposits", "project", "projects"})
LATIN_FOLDS = str.maketrans(
    {
        "ø": "o",
        "ł": "l",
        "đ": "d",
        "ð": "d",
        "þ": "th",
        "æ": "ae",
        "œ": "oe",
        "ı": "i",
    }
)
NEIGHBORS = tuple(itertools.product((-1, 0, 1), repeat=3))
# The padding only prevents floating-point grid-edge omissions. The final
# distance predicate is unpadded and uses each pair's smaller radius.
CELL_WIDTH = 2 * math.sin(MAX_RADIUS_KM / (2 * EARTH_RADIUS_KM)) + 1e-12


def _hash(value) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def normalize_name(value: str | None) -> str:
    """Fold case/diacritics and generic asset words; retain non-Latin characters.

    NFKD is Unicode normalization, not a complete transliteration algorithm.
    Source aliases provide explicit alternative spellings; none are invented.
    """
    if value is None:
        return ""
    if not isinstance(value, str):
        raise ValueError("Names and aliases must be strings or null")
    value = unicodedata.normalize("NFKD", value.casefold().translate(LATIN_FOLDS))
    value = "".join(c for c in value if not unicodedata.combining(c))
    tokens = "".join(c if c.isalnum() else " " for c in value).split()
    return " ".join(t for t in tokens if t not in GENERIC_NAME_WORDS)


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance on the IUGG mean-radius sphere, in kilometres."""
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    delta_phi = phi2 - phi1
    delta_lon = math.radians(lon2 - lon1)
    value = (
        math.sin(delta_phi / 2) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(delta_lon / 2) ** 2
    )
    return 2 * EARTH_RADIUS_KM * math.asin(math.sqrt(min(1.0, max(0.0, value))))


def _cell(lat: float, lon: float) -> tuple[int, int, int]:
    phi, lam = math.radians(lat), math.radians(lon)
    cos_phi = math.cos(phi)
    vector = (cos_phi * math.cos(lam), cos_phi * math.sin(lam), math.sin(phi))
    return tuple(math.floor(component / CELL_WIDTH) for component in vector)


def _strings(value, field: str) -> list[str]:
    if not isinstance(value, list) or any(not isinstance(v, str) for v in value):
        raise ValueError(f"{field} must be a list of strings")
    return value


def _asset(value: dict) -> dict:
    for field in ("record_id", "source_id", "precision_policy", "record_kind"):
        if not isinstance(value.get(field), str) or not value[field].strip():
            raise ValueError(f"Asset requires a nonblank {field}")
    coords = {}
    for field, bound in (("lat", 90), ("lon", 180)):
        raw = value.get(field)
        if isinstance(raw, bool) or not isinstance(raw, (int, float)):
            raise ValueError(f"Asset {field} must be a finite number")
        if not math.isfinite(raw) or not -bound <= raw <= bound:
            raise ValueError(f"Asset {field} is outside its valid range")
        coords[field] = float(raw)
    radius = value.get("radius_km")
    if isinstance(radius, bool) or radius not in (10, 25):
        raise ValueError("Eligible asset radius_km must be 10 or 25")
    families = frozenset(_strings(value.get("pilot_families"), "pilot_families"))
    if not families or families - PILOT_FAMILIES:
        raise ValueError("Eligible assets require only reviewed pilot families")
    aliases = _strings(value.get("aliases"), "aliases")
    names = tuple(
        sorted({n for s in [value.get("raw_name"), *aliases] if (n := normalize_name(s))})
    )
    return {
        "record_id": value["record_id"],
        "source_id": value["source_id"],
        "raw_name": value.get("raw_name"),
        "names": names,
        "tokens": tuple(frozenset(n.split()) for n in names),
        "families": families,
        "country_iso3s": tuple(sorted(set(_strings(value.get("country_iso3s"), "country_iso3s")))),
        "radius_km": float(radius),
        "precision_policy": value["precision_policy"],
        "record_kind": value["record_kind"],
        **coords,
    }


def _name_category(left: dict, right: dict) -> tuple[str, list[str], str | None]:
    if not left["names"] or not right["names"]:
        return "missing_name", [], None
    exact = sorted(set(left["names"]) & set(right["names"]))
    if exact:
        return "exact_normalized", exact[0].split(), exact[0]
    # Describe the strongest single pair of name variants. Do not combine
    # unrelated overlaps from different aliases into a stronger category.
    intersections = (a & b for a, b in itertools.product(left["tokens"], right["tokens"]))
    shared = min(intersections, key=lambda tokens: (-len(tokens), sorted(tokens)))
    if len(shared) >= 2:
        return "multiple_shared_tokens", sorted(shared), None
    if shared:
        return "one_shared_token", sorted(shared), None
    return "no_shared_tokens", [], None


def _distance_band(distance: float) -> str:
    if distance <= 1:
        return "0_to_1_km"
    if distance <= 5:
        return "over_1_to_5_km"
    if distance <= 10:
        return "over_5_to_10_km"
    return "over_10_to_25_km"


def _candidate(left: dict, right: dict, distance: float, shared: set[str]) -> dict:
    # Input records are traversed in identifier order, but keep this invariant
    # explicit for independently reused helper calls.
    if left["record_id"] > right["record_id"]:
        left, right = right, left
    category, tokens, exact = _name_category(left, right)
    return {
        "pair_id": "pair_" + _hash([left["record_id"], right["record_id"]]),
        "left_record_id": left["record_id"],
        "right_record_id": right["record_id"],
        "left_source_id": left["source_id"],
        "right_source_id": right["source_id"],
        "left_name": left["raw_name"],
        "right_name": right["raw_name"],
        "source_pair": sorted([left["source_id"], right["source_id"]]),
        "shared_families": sorted(shared),
        "left_country_iso3s": list(left["country_iso3s"]),
        "right_country_iso3s": list(right["country_iso3s"]),
        "distance_km": distance,
        "radius_km": min(left["radius_km"], right["radius_km"]),
        "left_precision_policy": left["precision_policy"],
        "right_precision_policy": right["precision_policy"],
        "distance_band": _distance_band(distance),
        "name_category": category,
        "exact_normalized_name": exact,
        "shared_name_tokens": tokens,
    }


def build_candidates(assets: list[dict]) -> list[dict]:
    """Block eligible assets spatially, then apply exact distance and family gates.

    The 3D unit-sphere grid has no longitude seam or polar special-case failure.
    A pair within 25 km differs by at most one cell along each axis. Only the 27
    adjacent cells are inspected; dense colocated groups necessarily emit many
    pairs. Same-source pairs remain eligible. Country is evidence, not a blocker.
    """
    records = sorted((_asset(value) for value in assets), key=lambda r: r["record_id"])
    if len({r["record_id"] for r in records}) != len(records):
        raise ValueError("Duplicate asset record_id")
    grid = defaultdict(list)
    pairs = []
    for right in records:
        cell = _cell(right["lat"], right["lon"])
        for offset in NEIGHBORS:
            neighbor = tuple(c + d for c, d in zip(cell, offset, strict=True))
            for left in grid.get(neighbor, ()):
                if not (shared := left["families"] & right["families"]):
                    continue
                distance = haversine_km(left["lat"], left["lon"], right["lat"], right["lon"])
                if distance <= min(left["radius_km"], right["radius_km"]):
                    pairs.append(_candidate(left, right, distance, shared))
        grid[cell].append(right)
    return sorted(pairs, key=lambda p: p["pair_id"])


def _stratum(pair: dict) -> tuple:
    return (
        tuple(pair["source_pair"]),
        tuple(pair["shared_families"]),
        pair["distance_band"],
        pair["name_category"],
    )


def _margins(stratum: tuple) -> set[tuple[str, str]]:
    sources, families, distance, name = stratum
    return {
        *(("source", source) for source in sources),
        *(("family", family) for family in families),
        ("source_pair", " | ".join(sources)),
        ("distance_band", distance),
        ("name_category", name),
    }


def select_gold(candidates: list[dict], size: int = 200, seed: str = "atlas-gold-v1") -> dict:
    """Choose an exact-size review sample without labels or trained similarities.

    First cover available marginal categories greedily, then distribute remaining
    quotas using square-root stratum counts. Within each stratum, SHA-256(seed,
    pair_id) gives a reproducible ordering independent of input row order. Quotas
    use descriptors only, and labels must be supplied later by human reviewers.
    """
    if isinstance(size, bool) or not isinstance(size, int) or size <= 0:
        raise ValueError("Sample size must be a positive integer")
    if not isinstance(seed, str) or not seed:
        raise ValueError("Sampling seed must be a nonblank string")
    if len({p["pair_id"] for p in candidates}) != len(candidates):
        raise ValueError("Duplicate candidate pair_id")
    identities = [tuple(sorted((p["left_record_id"], p["right_record_id"]))) for p in candidates]
    if any(left == right for left, right in identities) or len(set(identities)) != len(identities):
        raise ValueError("Self-pairs or duplicate unordered record pairs")
    if len(candidates) < size:
        raise ValueError(f"Need {size} distinct candidates; only {len(candidates)} are available")
    groups = defaultdict(list)
    for pair in candidates:
        groups[_stratum(pair)].append(pair)
    strata = sorted(groups)
    quotas = Counter({h: 0 for h in strata})
    ranks = {h: _hash([seed, "stratum", h]) for h in strata}
    margins = {h: _margins(h) for h in strata}
    available = set().union(*margins.values())
    uncovered = set(available)
    priorities = {
        "family": 5,
        "source": 3,
        "source_pair": 1,
        "distance_band": 3,
        "name_category": 4,
    }
    while uncovered and sum(quotas.values()) < size:
        choices = [h for h in strata if quotas[h] < len(groups[h]) and margins[h] & uncovered]
        if not choices:
            break
        chosen = min(
            choices,
            key=lambda h: (
                -sum(priorities[dimension] for dimension, _ in margins[h] & uncovered),
                ranks[h],
            ),
        )
        quotas[chosen] += 1
        uncovered -= margins[chosen]
    # When possible, represent every joint stratum before allocating extra rows.
    if len(strata) <= size:
        for h in strata:
            if not quotas[h]:
                quotas[h] = 1
    while sum(quotas.values()) < size:
        choices = [h for h in strata if quotas[h] < len(groups[h])]
        chosen = min(
            choices,
            key=lambda h: (
                -math.sqrt(len(groups[h])) / (quotas[h] + 1),
                ranks[h],
            ),
        )
        quotas[chosen] += 1
    selected, profiles = [], []
    for h in strata:
        source_pair, families, distance, name = h
        count, quota = len(groups[h]), quotas[h]
        stratum_id = "stratum_" + _hash(h)
        weight = count / quota if quota else None
        profiles.append(
            {
                "stratum_id": stratum_id,
                "source_pair": list(source_pair),
                "shared_families": list(families),
                "distance_band": distance,
                "name_category": name,
                "candidate_count": count,
                "sample_quota": quota,
                "sample_weight": weight,
            }
        )
        ranked = sorted(groups[h], key=lambda p: (_hash([seed, p["pair_id"]]), p["pair_id"]))
        for pair in ranked[:quota]:
            selected.append(
                {
                    **pair,
                    "stratum_id": stratum_id,
                    "stratum_candidate_count": count,
                    "stratum_sample_quota": quota,
                    "sample_weight": weight,
                    "label": "",
                    "reviewer": "",
                    "review_notes": "",
                }
            )
    selected.sort(key=lambda p: p["pair_id"])
    covered = set().union(*(_margins(_stratum(p)) for p in selected))
    coverage = {}
    for dimension in priorities:
        values = sorted(value for d, value in available if d == dimension)
        sampled = sorted(value for d, value in covered if d == dimension)
        coverage[dimension] = {
            "available": values,
            "sampled": sampled,
            "unrepresented": sorted(set(values) - set(sampled)),
        }
    return {
        "pairs": selected,
        "metadata": {
            "candidate_count": len(candidates),
            "sample_size": size,
            "seed": seed,
            "strata": profiles,
            "coverage": coverage,
            "sampling_method": (
                "marginal coverage then square-root stratum quotas; seeded hash order"
            ),
            "weight_definition": "candidate_count / sample_quota within each sampled joint stratum",
            "weight_limit": (
                "Unsampled joint strata have null weights. Weights describe this review design; "
                "they do not establish population precision or recall."
            ),
            "label_status": "blank_awaiting_human_review",
            "name_category_policy": (
                "Exact normalized aliases, multiple/one/no shared name tokens, or missing names; "
                "these are sampling categories, not match scores or predicted labels."
            ),
        },
    }


def prepare_review(assets: list[dict], size: int = 200, seed: str = "atlas-gold-v1") -> dict:
    """Return compact candidates, blank review rows, and complete sampling metadata."""
    candidates = build_candidates(assets)
    result = select_gold(candidates, size=size, seed=seed)
    result["metadata"].update(
        {
            "eligible_asset_count": len(assets),
            "earth_radius_km": EARTH_RADIUS_KM,
            "blocking_policy": (
                "3D unit-sphere grid; shared pilot family; distance <= min(pair radii)"
            ),
        }
    )
    return {"candidates": candidates, **result}
