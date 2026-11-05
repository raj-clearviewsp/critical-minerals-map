"""Freeze development-only matching, then independently evaluate sealed submissions."""

from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import tempfile
from collections import Counter, defaultdict
from importlib.metadata import version
from pathlib import Path

from .features import LEVELS, compare
from .metrics import evaluate
from .model import cross_validate, fit, reference_score, route, splink_scores

DIRECTORY = "data/review/matching-experiment-v1"
PROTOCOL = "experiments/matching/protocol.json"
ENV_LOCK = "experiments/matching/uv.lock"
FROZEN = ("model.json", "predictions.json", "development-evaluation.json", "features.json")


def encoded(value) -> bytes:
    return (json.dumps(value, sort_keys=True, indent=2, allow_nan=False) + "\n").encode()


def digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha(path: Path) -> str:
    return digest(path.read_bytes())


def save(root: Path, outputs: dict) -> None:
    for name, data in outputs.items():
        p = root / name
        if p.exists() and p.read_bytes() != data:
            raise ValueError(f"Refusing to replace frozen artifact: {name}")
    for name, data in outputs.items():
        p = root / name
        if p.exists():
            continue
        p.parent.mkdir(parents=True, exist_ok=True)
        fd, temp = tempfile.mkstemp(prefix=".matching-", dir=p.parent)
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(data)
            os.link(temp, p)
        finally:
            Path(temp).unlink(missing_ok=True)


def bound(root: Path, receipt_path: str, name: str) -> bytes:
    receipt = json.loads((root / receipt_path).read_text())
    raw = (root / name).read_bytes()
    if receipt["artifacts"][name] != {"sha256": digest(raw), "bytes": len(raw)}:
        raise ValueError(f"Input checksum changed: {name}")
    return raw


def model_implementation(root: Path) -> dict:
    names = [
        PROTOCOL,
        ENV_LOCK,
        *(f"pipeline/matching_lab/{n}.py" for n in ("features", "model", "metrics", "core")),
    ]
    return {name: sha(root / name) for name in names}


def training_inputs(root: Path) -> tuple:
    """Never reads the holdout CSV or the combined ledger containing holdout answers."""
    receipts = {
        "data/review/triage-v2/records.json": "reports/triage-audit.json",
        "data/review/triage-v2/triage-pairs.json": "reports/triage-audit.json",
        "data/review/reconciliation-v1/benchmark.json": "reports/reconciliation-audit.json",
        "data/review/development60-approved-v1/latest-decisions.json": (
            "reports/development60-accepted-audit.json"
        ),
        "data/review/next60-completed-v1/development.csv": "reports/next60-completed-audit.json",
    }
    content = {name: bound(root, receipt, name) for name, receipt in receipts.items()}
    records = json.loads(content["data/review/triage-v2/records.json"])
    candidates = json.loads(content["data/review/triage-v2/triage-pairs.json"])
    benchmark = json.loads(content["data/review/reconciliation-v1/benchmark.json"])["cases"]
    previous = json.loads(content["data/review/development60-approved-v1/latest-decisions.json"])[
        "decisions"
    ]
    newer = list(
        csv.DictReader(
            io.StringIO(
                content["data/review/next60-completed-v1/development.csv"].decode("utf-8-sig")
            )
        )
    )
    dev = {r["pair_id"] for r in benchmark if r["split"] == "development"}
    human = {r["pair_id"]: r for r in previous + newer}
    if len(dev) != 120 or not dev <= human.keys():
        raise ValueError("Development must have all 120 human answers")
    dev_components = {r["split_component"] for r in benchmark if r["split"] == "development"}
    test_components = {r["split_component"] for r in benchmark if r["split"] == "holdout"}
    if dev_components & test_components:
        raise ValueError("Connected neighborhoods cross the split")
    labels = {p: human[p]["label"] for p in sorted(dev)}
    by_pair = {r["pair_id"]: r for r in candidates}
    if len(by_pair) != len(candidates):
        raise ValueError("Candidate identities repeat")
    for p in dev:
        if any(human[p][k] != by_pair[p][k] for k in ("left_record_id", "right_record_id")):
            raise ValueError("Human label orientation differs from candidate")
    hashes = {name: digest(raw) for name, raw in content.items()}
    return records, candidates, benchmark, labels, hashes


def feature_rows(records: dict, candidates: list, benchmark: list) -> list:
    metadata = {r["pair_id"]: r for r in benchmark}
    rows = []
    for row in sorted(candidates, key=lambda r: r["pair_id"]):
        left, right = (records[row[key]] for key in ("left_record_id", "right_record_id"))
        feature = compare(left, right)
        if abs(feature["distance_km"] - row["distance_km"]) > 1e-7:
            raise ValueError("Recomputed source-point distance changed")
        selected = metadata.get(row["pair_id"], {})
        rows.append(
            {
                "pair_id": row["pair_id"],
                "left_record_id": row["left_record_id"],
                "right_record_id": row["right_record_id"],
                **feature,
                "component": selected.get("split_component"),
                "split": selected.get("split"),
                "stratum": selected.get("sampling_stratum"),
                "source_pair": sorted([left["source_id"], right["source_id"]]),
            }
        )
    return rows


def breakdown(rows: list, scores: dict, labels: dict) -> dict:
    groups = defaultdict(list)
    for row in rows:
        groups[row["stratum"] or "unspecified"].append(row)
    return {
        key: evaluate(
            group,
            {r["pair_id"]: scores[r["pair_id"]] for r in group},
            {r["pair_id"]: labels[r["pair_id"]] for r in group},
        )
        for key, group in sorted(groups.items())
    }


def freeze(root: Path) -> dict:
    records, candidates, benchmark, labels, hashes = training_inputs(root)
    rows = feature_rows(records, candidates, benchmark)
    development = [r for r in rows if r["split"] == "development"]
    if {r["pair_id"] for r in development} != set(labels):
        raise ValueError("Wrong training selection")
    model = fit(development, labels)
    scores = splink_scores(model, rows)
    dev_scores = {r["pair_id"]: scores[r["pair_id"]] for r in development}
    oof, folds = cross_validate(development, labels)
    ablations = {}
    for field in LEVELS:
        predictions, _ = cross_validate(development, labels, omitted=field)
        ablations[field] = evaluate(development, predictions, labels)
    sensitivity = {
        str(prior): evaluate(
            development,
            {r["pair_id"]: reference_score(model, r, prior=prior) for r in development},
            labels,
        )
        for prior in (0.01, 0.10, 0.50)
    }
    evaluation = {
        "in_sample": evaluate(development, dev_scores, labels),
        "group_cross_validation": evaluate(development, oof, labels),
        "folds": folds,
        "group_cv_by_stratum": breakdown(development, oof, labels),
        "leave_one_feature_out_group_cv": ablations,
        "fixed_prior_sensitivity_in_sample": sensitivity,
        "variant_selected_using_holdout": False,
    }
    predictions = {
        r["pair_id"]: {"score": scores[r["pair_id"]], "route": route(r, scores[r["pair_id"]])}
        for r in rows
    }
    outputs = {
        f"{DIRECTORY}/model.json": encoded(model),
        f"{DIRECTORY}/predictions.json": encoded(predictions),
        f"{DIRECTORY}/features.json": encoded(rows),
        f"{DIRECTORY}/development-evaluation.json": encoded(evaluation),
    }
    receipt = {
        "schema_version": "matching-model-freeze-v1",
        "inputs": hashes,
        "implementation": model_implementation(root),
        "versions": {p: version(p) for p in ("splink", "rapidfuzz", "duckdb", "pandas")},
        "candidate_pairs": len(rows),
        "training_cases": 120,
        "trained_on_holdout": False,
        "trained_on_agent47": False,
        "thresholds": {"auto_candidate": 0.95, "review": 0.70},
        "full_candidate_routes": dict(
            sorted(Counter(r["route"] for r in predictions.values()).items())
        ),
        "artifacts": {n: {"sha256": digest(v), "bytes": len(v)} for n, v in outputs.items()},
    }
    outputs[f"{DIRECTORY}/freeze.json"] = encoded(receipt)
    save(root, outputs)
    return receipt


def load_frozen(root: Path) -> tuple:
    receipt = json.loads((root / DIRECTORY / "freeze.json").read_text())
    if receipt["implementation"] != model_implementation(root):
        raise ValueError("Model implementation or protocol changed after freezing")
    for name, expected in receipt["inputs"].items():
        if sha(root / name) != expected:
            raise ValueError("Frozen training input changed")
    values = {}
    for name, meta in receipt["artifacts"].items():
        raw = (root / name).read_bytes()
        if {"sha256": digest(raw), "bytes": len(raw)} != meta:
            raise ValueError("Frozen model artifact changed")
        values[Path(name).name] = json.loads(raw)
    return receipt, values


def evaluate_human(root: Path) -> dict:
    frozen, values = load_frozen(root)
    # This is the first stage allowed to read individual holdout answers.
    name = "data/review/next60-completed-v1/holdout.csv"
    raw = bound(root, "reports/next60-completed-audit.json", name)
    human = list(csv.DictReader(io.StringIO(raw.decode("utf-8-sig"))))
    holdout = json.loads(
        bound(
            root, "reports/reconciliation-audit.json", "data/review/reconciliation-v1/holdout.json"
        )
    )
    if [r["pair_id"] for r in human] != [r["pair_id"] for r in holdout["cases"][:33]]:
        raise ValueError("Holdout test is not the original first33 in order")
    labels = {r["pair_id"]: r["label"] for r in human}
    rows = [r for r in values["features.json"] if r["pair_id"] in labels]
    if any(r["split"] != "holdout" for r in rows) or len(rows) != 33:
        raise ValueError("Holdout includes training data")
    scores = {r["pair_id"]: values["predictions.json"][r["pair_id"]]["score"] for r in rows}
    report = {
        "schema_version": "blind-human-evaluation-v1",
        "freeze_sha256": sha(root / DIRECTORY / "freeze.json"),
        "labels_sha256": digest(raw),
        "evaluation": evaluate(rows, scores, labels),
        "strata": breakdown(rows, scores, labels),
        "model_or_thresholds_changed": False,
        "blind_at_collection": True,
        "independent_population_precision_established": False,
        "errors": [
            {
                "pair_id": r["pair_id"],
                "human_label": labels[r["pair_id"]],
                "prediction": values["predictions.json"][r["pair_id"]],
                "guards": r["guards"],
            }
            for r in rows
            if labels[r["pair_id"]]
            in {"same_site", "different_sites", "left_contains_right", "right_contains_left"}
            and (
                (route(r, scores[r["pair_id"]]) == "auto_candidate")
                != (labels[r["pair_id"]] == "same_site")
            )
        ],
    }
    save(root, {f"{DIRECTORY}/human-evaluation.json": encoded(report)})
    return report


def evaluate_agent(root: Path, research_path: Path) -> dict:
    _, values = load_frozen(root)
    raw = research_path.read_bytes()
    research = json.loads(raw)
    proposed = research["cases"]
    holdout = json.loads(
        bound(
            root, "reports/reconciliation-audit.json", "data/review/reconciliation-v1/holdout.json"
        )
    )
    if [r["pair_id"] for r in proposed] != [r["pair_id"] for r in holdout["cases"][33:]]:
        raise ValueError("Assisted audit must cover the original final47 in order")
    labels = {r["pair_id"]: r["recommendation"] for r in proposed}
    rows = [r for r in values["features.json"] if r["pair_id"] in labels]
    scores = {r["pair_id"]: values["predictions.json"][r["pair_id"]]["score"] for r in rows}
    report = {
        "schema_version": "agent-assisted-audit-agreement-v1",
        "freeze_sha256": sha(root / DIRECTORY / "freeze.json"),
        "research_sha256": digest(raw),
        "agreement": evaluate(rows, scores, labels),
        "human_approved": False,
        "independent_blind_accuracy": False,
        "model_or_thresholds_changed": False,
        "interpretation": (
            "Agreement with researched agent proposals, not measured human-validated precision."
        ),
        "comparisons": [
            {
                "pair_id": r["pair_id"],
                "proposal": labels[r["pair_id"]],
                "prediction": values["predictions.json"][r["pair_id"]],
                "guards": r["guards"],
            }
            for r in rows
        ],
    }
    save(root, {f"{DIRECTORY}/agent-audit.json": encoded(report)})
    return report
