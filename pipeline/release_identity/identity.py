"""Separate reviewed entity equivalence, physical-site eligibility, and source observations."""

from __future__ import annotations

import hashlib
import math
import re
from collections import defaultdict
from itertools import combinations

from pipeline.reconciliation.ledger import UNRESOLVED
from pipeline.release_identity.constraints import conflicts
from pipeline.release_identity.registry import allocate

MIXED_NAME = re.compile(
    r"\b(?:project|includes?|district|complex|tenements?|operations|plants?|refiner(?:y|ies)|smelter|stockpile)\b",
    re.I,
)


def key(a, b):
    return tuple(sorted((a, b)))


def identity_graph(decisions, records):
    """Build connected components only from approved edges, then audit every implied comparison."""
    by_pair, adjacency = {}, defaultdict(set)
    for d in decisions:
        a, b = d["left_record_id"], d["right_record_id"]
        if a == b or a not in records or b not in records or key(a, b) in by_pair:
            raise ValueError("Invalid reviewed record pair")
        by_pair[key(a, b)] = d
        if d["label"] == "same_site":
            adjacency[a].add(b)
            adjacency[b].add(a)
    pending = set(adjacency)
    groups = []
    while pending:
        todo = [min(pending)]
        members = set()
        while todo:
            x = todo.pop()
            if x in members:
                continue
            members.add(x)
            todo.extend(adjacency[x] - members)
        pending -= members
        ordered = sorted(members)
        edges = []
        conflicts = []
        unchecked = []
        for a, b in combinations(ordered, 2):
            decision = by_pair.get((a, b))
            if decision is None:
                unchecked.append([a, b])
                continue
            if decision["label"] == "same_site":
                edges.append(decision["pair_id"])
            else:
                conflicts.append(
                    {
                        "pair_id": decision["pair_id"],
                        "left_record_id": a,
                        "right_record_id": b,
                        "label": decision["label"],
                    }
                )
        group_id = "group_" + hashlib.sha256("\0".join(ordered).encode()).hexdigest()
        groups.append(
            {
                "identity_group_id": group_id,
                "record_ids": ordered,
                "decision_pair_ids": sorted(edges),
                "conflicts": conflicts,
                "unchecked_internal_pairs": unchecked,
            }
        )
    return groups


def eligibility(row, parent_records):
    if row["entity_level"] != "site":
        return "source_scope_" + row["entity_level"]
    if row.get("scope_flags"):
        return "qualified_or_mixed_scope"
    if not row.get("entity_level_evidence") or not row.get("primary_name_usable"):
        return "missing_physical_site_evidence"
    if row["record_id"] in parent_records or MIXED_NAME.search(row.get("display_name") or ""):
        return "broader_or_mixed_asset"
    return location_problem(row) or "explicit_physical_site"


def location_problem(row):
    if row.get("location_precision") in {
        "none",
        "district",
        "country_centroid",
        "country centroid",
    }:
        return "insufficient_location"
    lat, lon = row.get("lat"), row.get("lon")
    if (
        not all(type(x) in (float, int) and math.isfinite(x) for x in (lat, lon))
        or not (-90 <= lat <= 90 and -180 <= lon <= 180)
        or (lat, lon) == (0, 0)
    ):
        return "invalid_location"
    return None


def anchor_key(row):
    provenance = row["provenance"]
    precision = {"exact": 0, "approximate": 1}.get(row.get("location_precision"), 2)
    return (
        precision,
        {"A": 0, "B": 1, "C": 2, "D": 3}.get(provenance.get("evidence_grade"), 4),
        not bool(provenance.get("retrieved_at")),
        row["record_id"],
    )


def project(records, previews, decisions, views, previous, release, scope_findings=None):
    scope_findings = scope_findings or {}
    if set(records) != set(previews) or any(k != r["record_id"] for k, r in records.items()):
        raise ValueError("Preview and source record identities differ")
    parents = {
        d["left_record_id"] if d["label"] == "left_contains_right" else d["right_record_id"]
        for d in decisions
        if d["label"] in {"left_contains_right", "right_contains_left"}
    }
    groups = identity_graph(decisions, records)
    group_by_record = {r: g for g in groups for r in g["record_ids"]}
    eligibility_by_id = {k: eligibility(r, parents) for k, r in records.items()}
    counterpart = {r: [other for other in g if other != r] for g in views for r in g}
    site_groups = []
    assigned = set()
    constraint_issues, quarantine = conflicts(decisions, groups, views)
    for g in groups:
        ids = g["record_ids"]
        g["physical_scope_findings"] = [
            scope_findings[p] for p in g["decision_pair_ids"] if p in scope_findings
        ]
        coords_countries = {c for r in ids for c in records[r].get("country_iso3s", [])}
        g["indirect_conflict_ids"] = [
            i["pair_id"] for i in constraint_issues if set(ids) & set(i["record_ids"])
        ]
        if (
            g["conflicts"]
            or g["unchecked_internal_pairs"]
            or g["indirect_conflict_ids"]
            or len(coords_countries) > 1
        ):
            g["disposition"] = "quarantine"
            quarantine.update(ids)
        elif all(eligibility_by_id[r] == "explicit_physical_site" for r in ids) or (
            len(ids) == 2
            and g["physical_scope_findings"]
            and all(location_problem(records[r]) is None for r in ids)
        ):
            g["disposition"] = "reviewed_physical_site"
            site_groups.append(ids)
            assigned.update(ids)
        else:
            g["disposition"] = "reviewed_named_identity_only"
    for r in sorted(records):
        if (
            eligibility_by_id[r] == "explicit_physical_site"
            and r not in assigned
            and r not in quarantine
        ):
            site_groups.append([r])
    site_ids, registry = allocate(site_groups, previous, release)
    by_record_decisions = defaultdict(list)
    uncertain = defaultdict(list)
    for d in decisions:
        for r in (d["left_record_id"], d["right_record_id"]):
            by_record_decisions[r].append(d["pair_id"])
            if d["label"] in UNRESOLVED:
                uncertain[r].append(d["pair_id"])
    members = []
    sites = []
    states = {}
    for ids in sorted(site_groups):
        anchor = min((records[r] for r in ids), key=anchor_key)
        findings = group_by_record[ids[0]]["physical_scope_findings"] if len(ids) > 1 else []
        if findings:
            if findings[0]["anchor_record_id"] not in ids:
                raise ValueError("Reviewed coordinate anchor is outside the approved pair")
            anchor = records[findings[0]["anchor_record_id"]]
        p = previews[anchor["record_id"]]
        if (anchor["lat"], anchor["lon"]) != (p["lat"], p["lon"]):
            raise ValueError("Preview changed a source coordinate")
        site_id = site_ids[ids[0]]
        reviewed = len(ids) > 1
        state = "reviewed_physical_site" if reviewed else "single_source_provisional"
        pair_ids = group_by_record[ids[0]]["decision_pair_ids"] if reviewed else []
        countries = sorted({c for r in ids for c in records[r].get("country_iso3s", [])})
        sites.append(
            {
                "site_id": site_id,
                "name": p["name"],
                "lat": p["lat"],
                "lon": p["lon"],
                "coordinate_record_id": anchor["record_id"],
                "location_precision": anchor.get("location_precision"),
                "country_iso3": countries[0] if len(countries) == 1 else None,
                "commodities": sorted({m for r in ids for m in previews[r]["minerals"]}),
                "decision_pair_ids": pair_ids,
                "record_ids": ids,
                "review_state": state,
                "source_url": p["source_url"],
                "provenance": {
                    **anchor["provenance"],
                    "entity_level_evidence": anchor["entity_level_evidence"],
                    "coordinate_basis": (
                        "Scope finding selects this source observation."
                        if findings
                        else "Stored source point; reported precision retained."
                    ),
                    "reviewed_scope_findings": findings,
                },
                "publisher_view_counterparts": sorted(
                    {other for r in ids for other in counterpart.get(r, []) if other not in ids}
                ),
                "unresolved_comparison_ids": sorted({u for r in ids for u in uncertain[r]}),
            }
        )
        for r in ids:
            states[r] = state
            members.append(
                {
                    "site_id": site_id,
                    "record_id": r,
                    "source_id": records[r]["source_id"],
                    "method": "manual" if reviewed else "source_observation",
                    "decision_pair_ids": pair_ids,
                }
            )
    observations = []
    for r, p in sorted(previews.items()):
        source = records[r]
        g = group_by_record.get(r)
        observations.append(
            {
                **p,
                "site_id": site_ids.get(r),
                "identity_group_id": g["identity_group_id"] if g else None,
                "review_state": states.get(
                    r, "quarantined_identity" if r in quarantine else "source_only"
                ),
                "physical_eligibility": (
                    "reviewed_physical_scope"
                    if states.get(r) == "reviewed_physical_site"
                    and g
                    and g["physical_scope_findings"]
                    else eligibility_by_id[r]
                ),
                "identity_review": "Has reviewed comparisons"
                if r in by_record_decisions
                else "No reviewed comparison",
                "reviewed_pair_ids": sorted(by_record_decisions[r]),
                "publisher_view_counterparts": counterpart.get(r, []),
                "evidence": {
                    k: source["provenance"].get(k)
                    for k in (
                        "source_file",
                        "source_table",
                        "source_row",
                        "raw_sha256",
                        "retrieved_at",
                        "registered_at",
                        "source_currency",
                        "evidence_grade",
                    )
                },
            }
        )
    return {
        "sites": sorted(sites, key=lambda r: r["site_id"]),
        "members": sorted(members, key=lambda r: r["record_id"]),
        "observations": observations,
        "identity-groups": groups,
        "constraint-conflicts": constraint_issues,
    }, registry
