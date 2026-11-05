"""Supervised FS likelihoods and Splink scoring on exact precomputed candidate pairs.

Each candidate gets two adapter rows carrying its source-only comparison levels.
Blocking on its unique pair key prevents accidental Cartesian comparisons. This
adapter is explicit because similarities include source-scoped alias lists, not
one flat string per entity. Splink computes the actual likelihood-ratio score.
"""

from __future__ import annotations

import hashlib
import math
from collections import Counter

from .features import LEVELS

POSITIVE = {"same_site"}
NEGATIVE = {"different_sites", "left_contains_right", "right_contains_left"}
UNRESOLVED = {"uncertain", "related_scope_unclear", "insufficient_evidence"}


def fit(rows: list, labels: dict, *, omitted: str | None = None) -> dict:
    ids = {r["pair_id"] for r in rows}
    if len(ids) != len(rows) or set(labels) != ids:
        raise ValueError("Training requires exact unique row/label coverage")
    if any(label not in POSITIVE | NEGATIVE | UNRESOLVED for label in labels.values()):
        raise ValueError("Unknown human relationship label")
    resolved = [r for r in rows if labels[r["pair_id"]] not in UNRESOLVED]
    positives = [r for r in resolved if labels[r["pair_id"]] in POSITIVE]
    negatives = [r for r in resolved if labels[r["pair_id"]] in NEGATIVE]
    if not positives or not negatives:
        raise ValueError("Both positive and negative training examples are required")
    comparisons = {}
    for field, count in LEVELS.items():
        if field == omitted:
            continue
        m = Counter(r["levels"][field] for r in positives)
        u = Counter(r["levels"][field] for r in negatives)
        if set(m) - set(range(count)) or set(u) - set(range(count)):
            raise ValueError("Invalid comparison level")
        comparisons[field] = {
            "m": [(m[i] + 1) / (len(positives) + count) for i in range(count)],
            "u": [(u[i] + 1) / (len(negatives) + count) for i in range(count)],
        }
    return {
        "schema_version": "supervised-fs-experiment-v1",
        "alpha": 1,
        "prior": (len(positives) + 1) / (len(resolved) + 2),
        "comparisons": comparisons,
        "training_counts": {
            "positive": len(positives),
            "negative": len(negatives),
            "abstention": len(rows) - len(resolved),
        },
        "training_pair_ids": sorted(ids),
        "probability_warning": (
            "Conditional score under a selected-sample model; "
            "uncalibrated for the candidate population."
        ),
    }


def reference_score(model: dict, row: dict, *, prior: float | None = None) -> float:
    prior = model["prior"] if prior is None else prior
    if not 0 < prior < 1:
        raise ValueError("Prior must be strictly between zero and one")
    log_odds = math.log(prior / (1 - prior))
    for field, rates in model["comparisons"].items():
        level = row["levels"][field]
        log_odds += math.log(rates["m"][level] / rates["u"][level])
    return 1 / (1 + math.exp(-log_odds))


def splink_scores(model: dict, rows: list) -> dict:
    import pandas as pd
    from splink import DuckDBAPI, Linker

    if len({r["pair_id"] for r in rows}) != len(rows):
        raise ValueError("Duplicate candidate key")
    if not rows:
        return {}
    comparisons = []
    for field, rates in model["comparisons"].items():
        levels = [
            {
                "sql_condition": f'"{field}_l" IS NULL OR "{field}_r" IS NULL',
                "is_null_level": True,
                "label_for_charts": "Invalid adapter null",
            }
        ]
        for i in range(len(rates["m"])):
            condition = f'"{field}_l" = {i}' if i < len(rates["m"]) - 1 else "ELSE"
            levels.append(
                {
                    "sql_condition": condition,
                    "m_probability": rates["m"][i],
                    "u_probability": rates["u"][i],
                    "label_for_charts": str(i),
                }
            )
        comparisons.append({"output_column_name": field, "comparison_levels": levels})
    records = []
    for row in sorted(rows, key=lambda r: r["pair_id"]):
        for side in ("a", "b"):
            records.append(
                {
                    "unique_id": row["pair_id"] + "|" + side,
                    "pair_key": row["pair_id"],
                    **{f: row["levels"][f] for f in model["comparisons"]},
                }
            )
    settings = {
        "link_type": "dedupe_only",
        "probability_two_random_records_match": model["prior"],
        "comparisons": comparisons,
        "retain_matching_columns": True,
        "blocking_rules_to_generate_predictions": ["l.pair_key = r.pair_key"],
        "additional_columns_to_retain": ["pair_key"],
    }
    import duckdb

    connection = duckdb.connect(config={"threads": 1})
    try:
        linker = Linker(
            pd.DataFrame(records), settings, DuckDBAPI(connection), set_up_basic_logging=False
        )
        predictions = linker.inference.predict().as_pandas_dataframe()
        result = {
            r["pair_key_l"]: float(r["match_probability"]) for r in predictions.to_dict("records")
        }
        if set(result) != {r["pair_id"] for r in rows} or len(predictions) != len(rows):
            raise ValueError("Splink returned a different candidate universe")
        for row in rows:
            if not math.isclose(result[row["pair_id"]], reference_score(model, row), abs_tol=1e-12):
                raise ValueError("Splink and independent FS calculation disagree")
        return result
    finally:
        connection.close()


def route(row: dict, score: float) -> str:
    if not math.isfinite(score) or not 0 <= score <= 1:
        raise ValueError("Score must be finite and within zero to one")
    if row["guards"]:
        return "scope_or_evidence_review"
    return (
        "auto_candidate"
        if score >= 0.95
        else "identity_review"
        if score >= 0.70
        else "keep_separate"
    )


def fold(component: str) -> int:
    return int(hashlib.sha256(("atlas-match-cv-v1:" + component).encode()).hexdigest()[:8], 16) % 5


def cross_validate(rows: list, labels: dict, *, omitted: str | None = None) -> tuple:
    scored, details = {}, []
    for i in range(5):
        training = [r for r in rows if fold(r["component"]) != i]
        testing = [r for r in rows if fold(r["component"]) == i]
        if not testing:
            details.append({"fold": i, "train": len(training), "test": 0})
            continue
        assert not {r["component"] for r in training} & {r["component"] for r in testing}
        model = fit(
            training, {r["pair_id"]: labels[r["pair_id"]] for r in training}, omitted=omitted
        )
        scored.update(splink_scores(model, testing))
        details.append(
            {
                "fold": i,
                "train": len(training),
                "test": len(testing),
                "training_counts": model["training_counts"],
            }
        )
    return scored, details
