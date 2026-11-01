"""Select grouped development/holdout cases without using their human label values."""

from __future__ import annotations

import hashlib
from collections import Counter, defaultdict

SEED = "atlas-validation-v1"
DEVELOPMENT_QUOTAS = {"likely_match": 3, "likely_distinct": 30, "relationship": 35, "ambiguous": 52}
HOLDOUT_QUOTAS = {"likely_match": 0, "likely_distinct": 26, "relationship": 27, "ambiguous": 27}


def rank(value: str) -> str:
    return hashlib.sha256((SEED + ":" + value).encode()).hexdigest()


def candidate_components(rows: list[dict], source_views: dict) -> dict[str, str]:
    """Connected candidate neighborhoods isolate splits, without declaring site identity."""
    parent = {}

    def find(value):
        parent.setdefault(value, value)
        while parent[value] != value:
            parent[value] = parent[parent[value]]
            value = parent[value]
        return value

    def union(a, b):
        a, b = find(a), find(b)
        if a != b:
            parent[max(a, b)] = min(a, b)

    for row in rows:
        union(row["left_record_id"], row["right_record_id"])
    for group in source_views["groups"]:
        for record in group[1:]:
            union(group[0], record)
    return {record: find(record) for record in sorted(parent)}


def stratum(row: dict) -> str:
    distance = row["distance_km"]
    band = "0–0.25 km" if distance <= 0.25 else "0.25–2 km" if distance <= 2 else "2–10 km"
    return " | ".join(
        [" / ".join(row["source_pair"]), ",".join(sorted(row["shared_families"])), band]
    )


def _diverse(pool: list[dict], count: int, salt: str) -> list[dict]:
    """Seeded round-robin across source/mineral/distance strata; not a probability sample."""
    groups = defaultdict(list)
    for row in pool:
        groups[stratum(row)].append(row)
    for group in groups.values():
        group.sort(key=lambda row: rank(salt + row["review_unit_id"]))
    keys = sorted(groups, key=lambda key: rank(salt + key))
    result = []
    while len(result) < count:
        progressed = False
        for key in keys:
            if groups[key] and len(result) < count:
                result.append(groups[key].pop(0))
                progressed = True
        if not progressed:
            raise ValueError(f"Not enough eligible cases for {salt}: {len(result)} of {count}")
    return result


def select_benchmark(
    rows: list[dict], units: dict, source_views: dict, prior_pair_ids: set[str]
) -> tuple[list[dict], dict]:
    """Freeze 120 development +80 held-out units; retain prior cases only in development.

    Entire connected candidate neighborhoods are assigned to a single split. This
    prevents known source views, shared records and neighboring candidate chains
    crossing the selected split. It is stricter than pair deduplication, but cannot
    prove absence of same-entity records missed by the original candidate block.
    """
    pairs = {row["pair_id"]: row for row in rows}
    if len(pairs) != len(rows) or not prior_pair_ids <= pairs.keys():
        raise ValueError("Duplicate candidates or unknown historical pair")
    components = candidate_components(rows, source_views)
    prior_components = {components[pairs[pair]["left_record_id"]] for pair in prior_pair_ids}
    representatives = []
    historical = []
    for unit in units["units"]:
        known = sorted(set(unit["member_pair_ids"]) & prior_pair_ids)
        pair_id = known[0] if known else unit["representative_pair_id"]
        row = {
            **pairs[pair_id],
            "queue": unit["queue"],
            "review_unit_id": unit["unit_id"],
            "equivalent_pair_ids": unit["member_pair_ids"],
            "split_component": components[pairs[pair_id]["left_record_id"]],
            "historical_pair_ids": known,
        }
        representatives.append(row)
        if known:
            historical.append(row)
    holdout = []
    for queue, count in HOLDOUT_QUOTAS.items():
        pool = [
            row
            for row in representatives
            if row["queue"] == queue and row["split_component"] not in prior_components
        ]
        holdout.extend(_diverse(pool, count, "holdout:" + queue))
    holdout_components = {row["split_component"] for row in holdout}
    development = list(historical)
    used = {row["review_unit_id"] for row in development}
    for queue, target in DEVELOPMENT_QUOTAS.items():
        count = target - sum(row["queue"] == queue for row in development)
        if count < 0:
            raise ValueError("Historical cases exceed a development quota")
        pool = [
            row
            for row in representatives
            if row["queue"] == queue
            and row["review_unit_id"] not in used
            and row["split_component"] not in holdout_components
        ]
        chosen = _diverse(pool, count, "development:" + queue)
        development.extend(chosen)
        used.update(row["review_unit_id"] for row in chosen)
    result = []
    for split, selected in (("development", development), ("holdout", holdout)):
        for row in sorted(selected, key=lambda row: rank(split + row["review_unit_id"])):
            result.append({**row, "split": split, "sampling_stratum": stratum(row)})
    validate_selection(result, prior_pair_ids)
    summary = {
        "seed": SEED,
        "size": len(result),
        "split_counts": dict(Counter(row["split"] for row in result)),
        "queue_counts_by_split": {
            split: dict(Counter(row["queue"] for row in result if row["split"] == split))
            for split in ("development", "holdout")
        },
        "retained_historical_units": len(historical),
        "new_units": len(result) - len(historical),
        "historical_source_view_pairs_excluded": sum(
            units["pair_to_unit"][pair] is None for pair in prior_pair_ids
        ),
        "candidate_components": len(set(components.values())),
        "selected_components_by_split": {
            split: len({row["split_component"] for row in result if row["split"] == split})
            for split in ("development", "holdout")
        },
        "source_pair_counts": dict(
            sorted(Counter(" / ".join(row["source_pair"]) for row in result).items())
        ),
        "family_counts": dict(Counter(f for row in result for f in row["shared_families"])),
        "sampling_policy": (
            "Fixed queue quotas with seeded source/mineral/distance diversity. Historical "
            "cases are retained in development only. This is not a probability sample; "
            "do not extrapolate unweighted performance to all candidates."
        ),
        "split_policy": (
            "No connected candidate neighborhood crosses development and holdout. "
            "Publisher views are consolidated. Blocking omissions can still cause leakage."
        ),
        "human_labels_created": 0,
        "model_fitted": False,
    }
    return result, summary


def validate_selection(rows: list[dict], prior_pair_ids: set[str]) -> None:
    if len(rows) != 200 or len({r["review_unit_id"] for r in rows}) != 200:
        raise ValueError("Validation set requires exactly 200 distinct review units")
    for split, quotas in (("development", DEVELOPMENT_QUOTAS), ("holdout", HOLDOUT_QUOTAS)):
        actual = Counter(r["queue"] for r in rows if r["split"] == split)
        if any(actual[q] != n for q, n in quotas.items()) or set(actual) - set(quotas):
            raise ValueError("Validation split quota mismatch")
    dev = {r["split_component"] for r in rows if r["split"] == "development"}
    holdout = {r["split_component"] for r in rows if r["split"] == "holdout"}
    if dev & holdout:
        raise ValueError("Candidate neighborhoods leak across the validation split")
    if any(set(r["equivalent_pair_ids"]) & prior_pair_ids for r in rows if r["split"] == "holdout"):
        raise ValueError("Previously reviewed cases cannot enter holdout")
