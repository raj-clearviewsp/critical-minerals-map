"""Engineering stress tests and candidate/cluster diagnostics; no ground truth invented."""

from __future__ import annotations

import copy
import itertools
from collections import Counter, defaultdict

from pipeline.link.candidates import haversine_km

from .core import DIRECTORY, bound, encoded, load_frozen, save, sha
from .features import compare, coordinates, names
from .model import reference_score, route, splink_scores


def cluster_audit(rows: list, scores: dict, records: dict, model: dict) -> dict:
    parent = {}

    def find(x):
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    edges = []
    for row in rows:
        if route(row, scores[row["pair_id"]]) != "auto_candidate":
            continue
        a, b = row["left_record_id"], row["right_record_id"]
        x, y = find(a), find(b)
        parent[max(x, y)] = min(x, y)
        edges.append(row)
    groups = defaultdict(list)
    for record in sorted(parent):
        groups[find(record)].append(record)
    details = []
    for members in groups.values():
        conflicts = []
        diameter = 0.0
        checked = 0
        if len(members) <= 100:
            for a, b in itertools.combinations(members, 2):
                feature = compare(records[a], records[b])
                checked += 1
                diameter = max(diameter, feature["distance_km"] or 0)
                if route(feature, reference_score(model, feature)) != "auto_candidate":
                    conflicts.append({"left": a, "right": b, "guards": feature["guards"]})
        details.append(
            {
                "members": members,
                "all_pairs_checked": checked,
                "diameter_km": diameter,
                "conflict_count": len(conflicts),
                "conflicts": conflicts,
                "quarantined": len(members) > 100 or bool(conflicts),
                "reason": "component_too_large_to_audit"
                if len(members) > 100
                else "transitive_link_not_supported_for_every_pair"
                if conflicts
                else "pairwise_consistent_but_not_approved",
            }
        )
    return {
        "proposed_edges": len(edges),
        "proposed_components": len(details),
        "quarantined_components": sum(c["quarantined"] for c in details),
        "largest_component": max((len(c["members"]) for c in details), default=0),
        "actual_merges": 0,
        "components": details,
    }


def blocking_audit(records: dict, rows: list) -> dict:
    existing = {tuple(sorted((r["left_record_id"], r["right_record_id"]))) for r in rows}
    groups = defaultdict(list)
    for key, record in records.items():
        for name in names(record):
            groups[name].append(key)
    checked = set()
    missing = []
    skipped = 0
    for members in groups.values():
        if len(members) > 200:
            skipped += 1
            continue
        for a, b in itertools.combinations(sorted(members), 2):
            if (a, b) in checked:
                continue
            checked.add((a, b))
            ra, rb = records[a], records[b]
            x, y = coordinates(ra), coordinates(rb)
            if (
                not x
                or not y
                or not set(ra.get("pilot_families", [])) & set(rb.get("pilot_families", []))
            ):
                continue
            distance = haversine_km(*x, *y)
            if distance <= 25 and (a, b) not in existing:
                missing.append(
                    {
                        "left": a,
                        "right": b,
                        "distance_km": distance,
                        "same_source": ra["source_id"] == rb["source_id"],
                    }
                )
    return {
        "diagnostic": (
            "Exact trusted-name/shared-mineral pairs within 25 km "
            "absent from original candidate block"
        ),
        "checked_name_pairs": len(checked),
        "skipped_large_name_groups": skipped,
        "missing_candidates": len(missing),
        "missing_within10km": sum(x["distance_km"] <= 10 for x in missing),
        "missing_cross_source": sum(not x["same_source"] for x in missing),
        "measured_blocking_recall": None,
        "pairs": missing,
        "limit": (
            "These are candidates for inspection, not proven missed matches; "
            "non-name matches remain unmeasured."
        ),
    }


def stress(root) -> dict:
    import json

    frozen, values = load_frozen(root)
    records = json.loads(
        bound(root, "reports/triage-audit.json", "data/review/triage-v2/records.json")
    )
    rows = values["features.json"]
    model = values["model.json"]
    scores = {p: v["score"] for p, v in values["predictions.json"].items()}
    # Full-universe pair reversal must preserve features and all decision guards.
    reversed_mismatches = []
    for row in rows:
        reversed_feature = compare(records[row["right_record_id"]], records[row["left_record_id"]])
        if any(reversed_feature[k] != row[k] for k in ("levels", "guards", "name_similarity")):
            reversed_mismatches.append(row["pair_id"])
    if reversed_mismatches:
        raise ValueError("Pair orientation changed matching features")
    benchmark = [r for r in rows if r["split"]]
    rescore = splink_scores(model, list(reversed(benchmark)))
    max_delta = max(abs(rescore[r["pair_id"]] - scores[r["pair_id"]]) for r in benchmark)
    if max_delta > 1e-12:
        raise ValueError("Row ordering changed Splink predictions")
    perturbations = {}
    for kind in (
        "punctuation_case",
        "remove_aliases",
        "jitter_0.001_degrees",
        "jitter_0.01_degrees",
    ):
        changed = 0
        maximum = 0.0
        directions = Counter()
        for row in benchmark:
            left = copy.deepcopy(records[row["left_record_id"]])
            right = copy.deepcopy(records[row["right_record_id"]])
            if kind == "punctuation_case":
                for record in (left, right):
                    if record.get("primary_name"):
                        record["primary_name"] = (
                            "  " + record["primary_name"].upper().replace(" ", " - ") + "  "
                        )
            elif kind == "remove_aliases":
                left["identity_aliases"] = []
                right["identity_aliases"] = []
            else:
                delta = 0.001 if kind == "jitter_0.001_degrees" else 0.01
                right["lat"] += delta
                right["lon"] = (right["lon"] + delta + 180) % 360 - 180
            feature = compare(left, right)
            score = reference_score(model, feature)
            old = route(row, scores[row["pair_id"]])
            new = route(feature, score)
            changed += old != new
            maximum = max(maximum, abs(score - scores[row["pair_id"]]))
            if old != new:
                directions[old + " -> " + new] += 1
        perturbations[kind] = {
            "cases": len(benchmark),
            "route_changes": changed,
            "largest_score_change": maximum,
            "transitions": dict(sorted(directions.items())),
        }
    if perturbations["punctuation_case"]["route_changes"]:
        raise ValueError("Harmless punctuation/case edits changed routes")
    report = {
        "schema_version": "matching-stress-audit-v1",
        "freeze_sha256": sha(root / DIRECTORY / "freeze.json"),
        "pair_reversal_cases": len(rows),
        "pair_reversal_failures": 0,
        "row_reorder_cases": len(benchmark),
        "row_reorder_largest_delta": max_delta,
        "perturbations": perturbations,
        "cluster_audit": cluster_audit(rows, scores, records, model),
        "blocking_audit": blocking_audit(records, rows),
        "scope_guard_counts": dict(sorted(Counter(g for r in rows for g in r["guards"]).items())),
        "interpretation": (
            "Engineering robustness and sensitivity, not empirical "
            "correctness of unreviewed records."
        ),
    }
    save(root, {f"{DIRECTORY}/stress-audit.json": encoded(report)})
    return report
