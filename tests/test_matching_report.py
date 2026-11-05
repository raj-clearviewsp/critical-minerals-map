"""Public-report integrity checks using entirely synthetic aggregate evidence."""

import json

import pytest

from pipeline.matching_lab import report
from pipeline.matching_lab.core import digest, encoded, sha
from pipeline.matching_lab.metrics import evaluate


def metrics(total, positive, unresolved, proposed=0):
    rows = [{"pair_id": str(i), "guards": []} for i in range(total)]
    labels = {
        str(i): "same_site"
        if i < positive
        else "related_scope_unclear"
        if i >= total - unresolved
        else "different_sites"
        for i in range(total)
    }
    scores = {str(i): 0.99 if i < proposed else 0.1 for i in range(total)}
    return evaluate(rows, scores, labels)


@pytest.fixture
def public_report(tmp_path):
    # No private source records, model, recommendations or human answers exist.
    for name in (
        "pipeline/matching_lab/report.py",
        "pipeline/link/candidates.py",
        report.ENV_LOCK,
        report.PROTOCOL,
        "reports/next60-completed-audit.json",
        "reports/final47-audit.json",
        "reports/linkage-review-audit.json",
    ):
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"synthetic immutable evidence\n")
    development = metrics(120, 30, 45, 3)
    receipt = {
        "schema_version": "matching-experiment-audit-v1",
        "status": "automatic_merge_gate_not_demonstrated",
        "candidate_pairs": 40,
        "versions": {"splink": "synthetic"},
        "full_candidate_routes": {"auto_candidate": 3, "scope_or_evidence_review": 37},
        "guard_counts": {"unresolved_entity_scope": 37},
        "implementation": report.implementation(tmp_path),
        "model_implementation": {},
        "model_frozen_sha256": "a" * 64,
        "source_helper_receipt_sha256": sha(tmp_path / "reports/linkage-review-audit.json"),
        "completed_receipt_sha256": sha(tmp_path / "reports/next60-completed-audit.json"),
        "research_receipt_sha256": sha(tmp_path / "reports/final47-audit.json"),
        "development_cv": development,
        "human33": metrics(33, 3, 20),
        "human_raw_unresolved_high": 0,
        "agent47": metrics(47, 12, 7, 2),
        "ablations": {"name": development},
        "prior_sensitivity": {"0.1": development},
        "stress": {
            "perturbations": {},
            "pair_reversal_cases": 40,
            "pair_reversal_failures": 0,
            "row_reorder_largest_delta": 0.0,
            "cluster_counts": {
                "proposed_edges": 3,
                "proposed_components": 3,
                "largest_component": 2,
                "quarantined_components": 0,
            },
            "blocking_counts": {
                "missing_candidates": 4,
                "missing_cross_source": 2,
                "missing_within10km": 1,
                "skipped_large_name_groups": 0,
            },
        },
        "gate_passed": False,
        "actual_merges": 0,
        "canonical_rows_added": 0,
        "formal_human_gold_complete": False,
        "trained_on_holdout": False,
        "trained_on_agent47": False,
    }
    rendered = report.render(receipt)
    receipt["artifacts"] = {
        report.REPORT: {"sha256": digest(rendered), "bytes": len(rendered)},
        f"{report.DIRECTORY}/freeze.json": {"sha256": digest(b"frozen"), "bytes": 6},
    }
    (tmp_path / report.REPORT).write_bytes(rendered)
    (tmp_path / report.RECEIPT).write_bytes(encoded(receipt))
    return tmp_path, receipt


def test_public_check_succeeds_without_any_private_artifacts(public_report, monkeypatch):
    root, expected = public_report
    assert not (root / "data").exists()

    def private_access_is_a_bug(*args, **kwargs):
        raise AssertionError("Offline CI must not load or recompute private evidence")

    monkeypatch.setattr(report, "load_frozen", private_access_is_a_bug)
    monkeypatch.setattr(report, "evaluate_human", private_access_is_a_bug)
    monkeypatch.setattr(report, "evaluate_agent", private_access_is_a_bug)
    assert report.check(root) == expected


@pytest.mark.parametrize(
    "name",
    [
        "pipeline/matching_lab/report.py",
        "pipeline/link/candidates.py",
        report.ENV_LOCK,
        report.PROTOCOL,
        "reports/next60-completed-audit.json",
        "reports/final47-audit.json",
        "reports/linkage-review-audit.json",
    ],
)
def test_public_check_rejects_changed_code_protocol_or_receipt(public_report, name):
    root, _ = public_report
    (root / name).write_bytes(b"changed")
    with pytest.raises(ValueError, match="receipt or implementation changed"):
        report.check(root)


@pytest.mark.parametrize("kind", ["report", "checksum", "gate", "count", "status"])
def test_public_check_rejects_report_and_claim_tampering(public_report, kind):
    root, receipt = public_report
    if kind == "report":
        (root / report.REPORT).write_bytes(b"The precision gate passed.")
    elif kind == "checksum":
        receipt["artifacts"][report.REPORT]["sha256"] = "0" * 64
    elif kind == "gate":
        receipt["gate_passed"] = True
    elif kind == "status":
        receipt["status"] = "automatic_merge_approved"
    else:
        receipt["human33"]["cases"] = 34
    (root / report.RECEIPT).write_bytes(encoded(receipt))
    with pytest.raises(ValueError):
        report.check(root)


def test_local_check_detects_private_tampering_before_rebuilding(public_report, monkeypatch):
    root, _ = public_report
    path = root / report.DIRECTORY / "freeze.json"
    path.parent.mkdir(parents=True)
    path.write_bytes(b"tampered")

    def unexpected_rebuild(*args, **kwargs):
        raise AssertionError("Corrupt private artifacts must fail before rebuilding")

    monkeypatch.setattr(report, "prepare", unexpected_rebuild)
    with pytest.raises(ValueError, match="Private matching artifact changed"):
        report.check(root, local=True)


def test_render_exposes_unresolved_denominator_and_undefined_precision(public_report):
    _, receipt = public_report
    receipt["human33"]["unresolved_auto_candidates"] = 2
    rendered = report.render(receipt).decode()
    assert "undefined (no eligible predictions)" in rendered
    assert "Auto-match denominator includes resolved comparisons only" in rendered
    assert "blind human 2" in rendered
    assert "within 10 km" in rendered
    assert "not independent human accuracy" in rendered
    # Serialization of the public aggregate cannot introduce individual cases.
    assert "pair_id" not in json.dumps(receipt)
