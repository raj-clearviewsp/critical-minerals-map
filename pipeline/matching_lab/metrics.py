"""Explicit denominators, abstention and nonidentity relationship accounting."""

import math
from collections import Counter

from .model import NEGATIVE, POSITIVE, UNRESOLVED, route


def wilson(successes: int, total: int) -> list | None:
    if not 0 <= successes <= total:
        raise ValueError("Invalid interval counts")
    if total == 0:
        return None
    z = 1.959963984540054
    p = successes / total
    denominator = 1 + z * z / total
    center = (p + z * z / (2 * total)) / denominator
    half = z * math.sqrt((p * (1 - p) + z * z / (4 * total)) / total) / denominator
    return [max(0, center - half), min(1, center + half)]


def evaluate(rows: list, scores: dict, labels: dict) -> dict:
    ids = {r["pair_id"] for r in rows}
    if set(scores) != ids or set(labels) != ids or len(ids) != len(rows):
        raise ValueError("Evaluation requires exact row, score and label alignment")
    if any(x not in POSITIVE | NEGATIVE | UNRESOLVED for x in labels.values()):
        raise ValueError("Unknown evaluation label")
    counts = Counter(labels.values())
    tp = fp = fn = tn = unresolved_positive = raw_tp = raw_fp = 0
    routes = Counter()
    for row in rows:
        pair = row["pair_id"]
        label = labels[pair]
        score = scores[pair]
        decision = route(row, score)
        routes[decision] += 1
        predicted = decision == "auto_candidate"
        if label in UNRESOLVED:
            unresolved_positive += predicted
            continue
        positive = label in POSITIVE
        tp += predicted and positive
        fp += predicted and not positive
        fn += not predicted and positive
        tn += not predicted and not positive
        raw_tp += score >= 0.95 and positive
        raw_fp += score >= 0.95 and not positive
    return {
        "cases": len(rows),
        "labels": dict(sorted(counts.items())),
        "resolved_identity_cases": tp + fp + fn + tn,
        "abstentions": sum(counts[x] for x in UNRESOLVED),
        "containment_cases": counts["left_contains_right"] + counts["right_contains_left"],
        "routes": dict(sorted(routes.items())),
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "tn": tn,
        "precision": tp / (tp + fp) if tp + fp else None,
        "recall_within_resolved_sample": tp / (tp + fn) if tp + fn else None,
        "precision_wilson95": wilson(tp, tp + fp),
        "recall_wilson95": wilson(tp, tp + fn),
        "unresolved_auto_candidates": unresolved_positive,
        "raw_score_tp": raw_tp,
        "raw_score_fp": raw_fp,
        "raw_score_precision": raw_tp / (raw_tp + raw_fp) if raw_tp + raw_fp else None,
        "population_accuracy_claim": False,
    }
