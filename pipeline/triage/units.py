"""Deduplicate reviewer work across verified publisher views, never source observations."""

from __future__ import annotations

import hashlib
import json
from collections import Counter, defaultdict

QUEUES = frozenset({"likely_match", "likely_distinct", "relationship", "ambiguous"})


class ReviewUnitError(ValueError):
    """Source-view reconciliation or candidate identities are inconsistent."""


def _identity(value, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ReviewUnitError(f"{field} must be a nonblank string")
    return value


def _view_groups(source_views: dict) -> dict[str, str]:
    """Validate disjoint groups against their connected, explicitly verified view pairs."""
    if not isinstance(source_views, dict):
        raise ReviewUnitError("Source views must be an object")
    groups, pairs = source_views.get("groups"), source_views.get("pairs")
    if not isinstance(groups, list) or not isinstance(pairs, list):
        raise ReviewUnitError("Source views require groups and pairs lists")
    representatives, group_sets = {}, []
    for group in groups:
        if not isinstance(group, list) or len(group) < 2:
            raise ReviewUnitError("Each source-view group needs at least two distinct IDs")
        ids = [_identity(value, "Source-view record ID") for value in group]
        members = set(ids)
        if len(members) != len(ids) or members & representatives.keys():
            raise ReviewUnitError("Source-view groups must be disjoint with unique IDs")
        representative = min(members)
        representatives.update(dict.fromkeys(members, representative))
        group_sets.append(members)
    edges, adjacency = set(), defaultdict(set)
    for pair in pairs:
        if not isinstance(pair, dict):
            raise ReviewUnitError("Source-view pair must be an object")
        left = _identity(pair.get("left_record_id"), "Left source-view ID")
        right = _identity(pair.get("right_record_id"), "Right source-view ID")
        identity = tuple(sorted((left, right)))
        if left == right or identity in edges:
            raise ReviewUnitError("Repeated or self source-view pair")
        if (
            left not in representatives
            or right not in representatives
            or representatives[left] != representatives[right]
            or pair.get("reason") != "same_publisher_release_view"
            or not isinstance(pair.get("evidence"), dict)
        ):
            raise ReviewUnitError("Source-view pair is inconsistent with its verified group")
        edges.add(identity)
        adjacency[left].add(right)
        adjacency[right].add(left)
    for members in group_sets:
        pending, reached = [min(members)], set()
        while pending:
            current = pending.pop()
            if current not in reached:
                reached.add(current)
                pending.extend(adjacency[current] - reached)
        if reached != members:
            raise ReviewUnitError("Source-view group is not supported by connected verified pairs")
    return representatives


def _unit_id(identity: tuple[str, str]) -> str:
    content = json.dumps(identity, ensure_ascii=False, separators=(",", ":")).encode()
    return "review_unit_" + hashlib.sha256(content).hexdigest()


def build_review_units(rows: list[dict], source_views: dict) -> dict:
    """Collapse duplicate comparisons arising solely from verified publisher views.

    This is workload bookkeeping. Source rows, candidate rows, identifiers and
    human decisions are never rewritten. Same-coordinate or same-name records
    are not grouped unless the separate publisher-view reconciliation proves it.
    Internal source-view comparisons remain in the audit but need no site review.
    """
    if not isinstance(rows, list):
        raise ReviewUnitError("Triage rows must be a list")
    representatives = _view_groups(source_views)
    grouped, pair_to_unit, internal = defaultdict(list), {}, []
    seen_pairs, encountered_records = set(), set()
    for row in rows:
        if not isinstance(row, dict):
            raise ReviewUnitError("Each triage row must be an object")
        pair_id = _identity(row.get("pair_id"), "Pair ID")
        left = _identity(row.get("left_record_id"), "Left record ID")
        right = _identity(row.get("right_record_id"), "Right record ID")
        identity = tuple(sorted((left, right)))
        if pair_id in pair_to_unit or identity in seen_pairs or left == right:
            raise ReviewUnitError("Duplicate candidate identity or self-pair")
        if row.get("queue") not in QUEUES:
            raise ReviewUnitError("Unrecognized triage queue")
        reasons = row.get("reason_codes")
        if not isinstance(reasons, list) or any(not isinstance(reason, str) for reason in reasons):
            raise ReviewUnitError("Routing reasons must be a string list")
        seen_pairs.add(identity)
        encountered_records.update(identity)
        canonical = tuple(
            sorted((representatives.get(left, left), representatives.get(right, right)))
        )
        if canonical[0] == canonical[1]:
            pair_to_unit[pair_id] = None
            internal.append(pair_id)
        else:
            identity_id = _unit_id(canonical)
            pair_to_unit[pair_id] = identity_id
            grouped[canonical].append(row)
    if set(representatives) - encountered_records:
        raise ReviewUnitError("Source-view groups reference records absent from candidates")
    units = []
    for canonical, members in sorted(grouped.items()):
        pair_ids = sorted(row["pair_id"] for row in members)
        counts = Counter(row["queue"] for row in members)
        conflict = len(counts) > 1
        queue = "ambiguous" if conflict else next(iter(counts))
        reasons = {reason for row in members for reason in row["reason_codes"]}
        if conflict:
            reasons.add("publisher_view_routing_disagreement")
        units.append(
            {
                "unit_id": _unit_id(canonical),
                "canonical_record_ids": list(canonical),
                "member_pair_ids": pair_ids,
                "representative_pair_id": pair_ids[0],
                "member_pair_count": len(pair_ids),
                "queue": queue,
                "reason_codes": sorted(reasons),
                "queue_conflict": conflict,
                "member_queue_counts": dict(sorted(counts.items())),
                "requires_adjudication": queue in {"relationship", "ambiguous"},
                "auto_merge_eligible": False,
            }
        )
    units.sort(key=lambda unit: unit["unit_id"])
    summary = {
        "input_candidate_count": len(rows),
        "source_view_internal_pairs": len(internal),
        "review_units": len(units),
        "duplicate_comparisons_collapsed": len(rows) - len(internal) - len(units),
        "unit_queue_counts": dict(sorted(Counter(unit["queue"] for unit in units).items())),
        "units_with_routing_disagreement": sum(unit["queue_conflict"] for unit in units),
        "policy": (
            "Reviewer workload only: verified publisher source views share a comparison unit; "
            "all original source and candidate observations remain unchanged."
        ),
    }
    return {
        "units": units,
        "pair_to_unit": dict(sorted(pair_to_unit.items())),
        "internal_pair_ids": sorted(internal),
        "summary": summary,
    }
