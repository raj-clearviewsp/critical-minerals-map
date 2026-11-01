"""Accepted validation packets bind exact exports and reject incomplete or invalid receipts."""

import copy
import csv
import io
import json

import pytest

from pipeline.reconciliation import core
from pipeline.reconciliation.ledger import DIRECTORY
from pipeline.scaffold import digest, json_bytes
from pipeline.triage.labels import blank_csv, sample_digest


def case(number=0):
    return {
        "pair_id": f"pair_{number}",
        "left_record_id": f"a:{number}",
        "right_record_id": f"b:{number}",
        "distance_km": 1.1,
        "shared_families": ["Ni"],
        "queue": "likely_distinct",
        "features": {"secret": 1},
        "previous_decision": {"label": "same_site", "reviewer": "Previous reviewer"},
        "left": {
            "record_id": f"a:{number}",
            "source_id": "a",
            "display_name": "Source A",
            "lat": 0,
            "lon": 0,
        },
        "right": {
            "record_id": f"b:{number}",
            "source_id": "b",
            "display_name": "Source B",
            "lat": 0,
            "lon": 0.01,
        },
    }


def packet(count=3):
    result = {
        "schema_version": "validation-review-v1",
        "mode": "holdout",
        "cases": [case(i) for i in range(count)],
    }
    result["sample_sha256"] = sample_digest(result)
    return result


def csv_export(sample, labels=None):
    text = blank_csv(sample["cases"], sample["sample_sha256"]).decode()
    rows = list(csv.DictReader(io.StringIO(text)))
    for row, label in zip(rows, labels or [""] * len(rows), strict=True):
        row["label"] = label
        row["reviewer"] = "Reviewer" if label else ""
    result = io.StringIO(newline="")
    writer = csv.DictWriter(result, fieldnames=list(rows[0]), lineterminator="\r\n")
    writer.writeheader()
    writer.writerows(rows)
    return b"\xef\xbb\xbf" + result.getvalue().encode()


@pytest.fixture
def receipt():
    names = [
        "completed-pilot.csv",
        "decision-history.json",
        "latest-decisions.json",
        "pilot-outcomes.json",
        "source-research-input.json",
        "source-research.json",
        "benchmark.json",
        "index.html",
    ]
    names += [
        mode + suffix for mode in core.PACKET_MODES for suffix in (".json", "-blank.csv", ".html")
    ]
    artifacts = {f"{DIRECTORY}/{name}": {"sha256": digest(b"{}"), "bytes": 2} for name in names}
    artifacts[core.REPORT] = {"sha256": digest(b"synthetic report\n"), "bytes": 17}
    ledger = {
        "schema_version": "review-ledger-summary-v1",
        "parent_triage_receipt_sha256": "a" * 64,
        "original_csv_sha256": "b" * 64,
        "completed_csv_sha256": "c" * 64,
        "pilot_sample_sha256": "d" * 64,
        "original_decisions": 20,
        "pilot_decisions": 20,
        "history_events": 40,
        "unique_reviewed_pairs": 34,
        "repeated_pairs": 6,
        "changed_labels": 1,
        "unchanged_repeated_labels": 5,
        "pilot_label_counts": {
            "same_site": 14,
            "different_sites": 2,
            "left_contains_right": 1,
            "related_scope_unclear": 2,
            "insufficient_evidence": 1,
        },
        "pilot_queue_by_label": {
            "likely_match": {"same_site": 3},
            "likely_distinct": {"same_site": 1, "different_sites": 2},
            "relationship": {
                "same_site": 4,
                "left_contains_right": 1,
                "related_scope_unclear": 1,
                "insufficient_evidence": 1,
            },
            "ambiguous": {"same_site": 6, "related_scope_unclear": 1},
        },
        "latest_label_counts": {
            "same_site": 19,
            "different_sites": 7,
            "left_contains_right": 1,
            "uncertain": 4,
            "related_scope_unclear": 2,
            "insufficient_evidence": 1,
        },
        "pilot_unresolved": 3,
        "latest_unresolved": 7,
        "rule_disagreements": 1,
        "formal_gold_set_complete": False,
        "accuracy_metrics_reported": False,
        "model_fitted": False,
        "auto_merges": 0,
        "artifacts": {
            f"{DIRECTORY}/{name}": artifacts[f"{DIRECTORY}/{name}"] for name in names[:4]
        },
    }
    return {
        "schema_version": "reconciliation-audit-v1",
        "phase": 0,
        "step": 6,
        "status": "evidence_and_validation_prepared_human_review_pending",
        "parent_receipt_sha256": "a" * 64,
        "parent_sample_sha256": "d" * 64,
        "ledger": ledger,
        "selection": {
            "size": 200,
            "split_counts": {"development": 120, "holdout": 80},
            "retained_historical_units": 33,
            "new_units": 167,
            "historical_source_view_pairs_excluded": 1,
        },
        "research": {
            "researched_cases": 9,
            "findings": 12,
            "disposition_counts": {"human_adjudication": 7, "research_open": 1, "corroborated": 1},
            "proposal_counts": {"different_sites": 7, "same_site": 1, "related_scope_unclear": 1},
            "human_labels_changed": 0,
        },
        "historical_readiness": {
            "prior_decision_available": 24,
            "human_adjudication": 7,
            "research_open": 1,
            "corroborated": 1,
        },
        "packets": {
            mode: {"cases": count, "sample_sha256": "e" * 64}
            for mode, count in [("adjudication", 7), ("development", 87), ("holdout", 80)]
        },
        "benchmark_sha256": "f" * 64,
        "implementation": {},
        "model_fitted": False,
        "auto_merges": 0,
        "human_labels_created": 0,
        "canonical_rows_added": 0,
        "published_rows_added": 0,
        "formal_gold_set_complete": False,
        "artifacts": artifacts,
    }


def test_valid_receipt_reconciles_all_artifacts_and_counts(receipt):
    core.validate_receipt(receipt)


@pytest.mark.parametrize("mutation", ["missing", "extra", "unsafe", "bad_hash", "bad_size"])
def test_receipt_requires_the_complete_exact_artifact_manifest(receipt, mutation):
    artifacts = receipt["artifacts"]
    key = f"{DIRECTORY}/holdout.json"
    if mutation == "missing":
        artifacts.pop(key)
    elif mutation == "extra":
        artifacts[f"{DIRECTORY}/surprise.json"] = {"sha256": "a" * 64, "bytes": 1}
    elif mutation == "unsafe":
        artifacts[f"{DIRECTORY}/../escape.json"] = artifacts.pop(key)
    elif mutation == "bad_hash":
        artifacts[key]["sha256"] = "bad"
    else:
        artifacts[key]["bytes"] = True
    with pytest.raises(ValueError):
        core.validate_receipt(receipt)


@pytest.mark.parametrize(
    "mutation",
    [
        "negative_readiness",
        "negative_research",
        "bool_count",
        "float_count",
        "bad_packet_hash",
        "bad_parent_hash",
    ],
)
def test_receipt_rejects_invalid_nested_counts_and_hashes(receipt, mutation):
    if mutation == "negative_readiness":
        receipt["historical_readiness"] = {"prior_decision_available": 34, "research_open": -1}
    elif mutation == "negative_research":
        receipt["research"]["disposition_counts"] = {
            "human_adjudication": 7,
            "research_open": -1,
            "corroborated": 3,
        }
    elif mutation == "bool_count":
        receipt["research"]["human_labels_changed"] = False
    elif mutation == "float_count":
        receipt["selection"]["new_units"] = 167.0
    elif mutation == "bad_packet_hash":
        receipt["packets"]["holdout"]["sample_sha256"] = "bad"
    else:
        receipt["parent_receipt_sha256"] = "bad"
    with pytest.raises(ValueError):
        core.validate_receipt(receipt)


def test_report_file_must_match_its_manifest_entry(tmp_path, monkeypatch, receipt):
    monkeypatch.setattr(core, "check_triage", lambda root: {"sample_sha256": "d" * 64})
    monkeypatch.setattr(core, "implementation_hashes", lambda root: {})
    monkeypatch.setattr(core, "render_report", lambda value: b"synthetic report\n")
    (tmp_path / "reports").mkdir()
    (tmp_path / "reports/triage-audit.json").write_bytes(b"parent")
    receipt["parent_receipt_sha256"] = digest(b"parent")
    receipt["ledger"]["parent_triage_receipt_sha256"] = receipt["parent_receipt_sha256"]
    receipt["artifacts"][core.REPORT]["sha256"] = "b" * 64
    (tmp_path / core.RECEIPT).write_bytes(json_bytes(receipt))
    (tmp_path / core.REPORT).write_bytes(b"synthetic report\n")
    with pytest.raises(ValueError):
        core.check(tmp_path)


def test_packet_build_blinds_json_as_well_as_html_and_starts_blank():
    outputs, info = core._packet([case(), case(1)], "holdout", {"a": "Publisher A"})
    sample = json.loads(outputs[f"{DIRECTORY}/holdout.json"])
    assert sample["sample_sha256"] == sample_digest(sample) == info["sample_sha256"]
    for item in sample["cases"]:
        assert "queue" not in item and "features" not in item and "previous_decision" not in item
    assert b"Previous reviewer" not in outputs[f"{DIRECTORY}/holdout.html"]
    rows = list(csv.DictReader(io.StringIO(outputs[f"{DIRECTORY}/holdout-blank.csv"].decode())))
    assert len(rows) == 2 and all(row["label"] == "" for row in rows)
    assert all(row["sample_sha256"] == sample["sample_sha256"] for row in rows)


def test_export_completion_is_not_resolution_or_accuracy(tmp_path):
    sample = packet()
    path = tmp_path / "review.csv"
    path.write_bytes(
        csv_export(sample, ["same_site", "insufficient_evidence", "left_contains_right"])
    )
    result = core.validate_export(path, sample, complete=True)
    assert result["all_cases_reviewed"] is True
    assert result["formal_gold_set_complete"] is False
    assert result["counts"].get("blank", 0) == 0
    assert {key: count for key, count in result["counts"].items() if count} == {
        "same_site": 1,
        "insufficient_evidence": 1,
        "left_contains_right": 1,
    }


@pytest.mark.parametrize(
    "mutation",
    [
        "blank",
        "wrong_hash",
        "wrong_id",
        "unknown_label",
        "duplicate",
        "missing",
        "missing_reviewer",
    ],
)
def test_export_rejects_incomplete_stale_or_changed_decisions(tmp_path, mutation):
    sample = packet()
    rows = list(
        csv.DictReader(io.StringIO(csv_export(sample, ["same_site"] * 3).decode("utf-8-sig")))
    )
    if mutation == "blank":
        rows[0]["label"] = ""
    elif mutation == "wrong_hash":
        rows[0]["sample_sha256"] = "b" * 64
    elif mutation == "wrong_id":
        rows[0]["left_record_id"] = "wrong"
    elif mutation == "unknown_label":
        rows[0]["label"] = "likely_match"
    elif mutation == "duplicate":
        rows[-1] = copy.deepcopy(rows[0])
    elif mutation == "missing":
        rows.pop()
    else:
        rows[0]["reviewer"] = ""
    text = io.StringIO(newline="")
    writer = csv.DictWriter(text, fieldnames=list(rows[0]))
    writer.writeheader()
    writer.writerows(rows)
    path = tmp_path / "review.csv"
    path.write_text(text.getvalue())
    with pytest.raises(ValueError):
        core.validate_export(path, sample, complete=True)


@pytest.mark.parametrize("mutation", ["empty", "too_many", "self_pair", "duplicate_pair"])
def test_export_rejects_structurally_invalid_self_hashed_packets(tmp_path, mutation):
    sample = packet(201 if mutation == "too_many" else 3)
    if mutation == "empty":
        sample["cases"] = []
    elif mutation == "self_pair":
        sample["cases"][0]["right_record_id"] = sample["cases"][0]["left_record_id"]
    elif mutation == "duplicate_pair":
        sample["cases"][1] = {**copy.deepcopy(sample["cases"][0]), "pair_id": "another_id"}
    sample["sample_sha256"] = sample_digest(sample)
    path = tmp_path / "review.csv"
    path.write_bytes(blank_csv(sample["cases"], sample["sample_sha256"]))
    with pytest.raises(ValueError):
        core.validate_export(path, sample)


@pytest.fixture
def bound_packet(tmp_path):
    sample = packet()
    relative = f"{DIRECTORY}/holdout.json"
    path = tmp_path / relative
    path.parent.mkdir(parents=True)
    content = json_bytes(sample)
    path.write_bytes(content)
    receipt = {
        "artifacts": {relative: {"sha256": digest(content), "bytes": len(content)}},
        "packets": {"holdout": {"cases": 3, "sample_sha256": sample["sample_sha256"]}},
    }
    return tmp_path, receipt, sample, path


def test_load_packet_accepts_only_bound_sample_bytes(bound_packet):
    root, receipt, sample, _ = bound_packet
    assert core.load_packet(root, receipt, "holdout") == sample


@pytest.mark.parametrize(
    "mutation", ["changed_bytes", "changed_sample", "changed_mode", "wrong_size"]
)
def test_load_packet_rejects_modified_private_packet(bound_packet, mutation):
    root, receipt, sample, path = bound_packet
    relative = f"{DIRECTORY}/holdout.json"
    if mutation == "changed_bytes":
        path.write_bytes(path.read_bytes() + b"\n")
    elif mutation == "wrong_size":
        receipt["artifacts"][relative]["bytes"] += 1
    else:
        if mutation == "changed_sample":
            sample["cases"][0]["distance_km"] += 1
        else:
            sample["mode"] = "development"
        sample["sample_sha256"] = sample_digest(sample)
        content = json_bytes(sample)
        path.write_bytes(content)
        receipt["artifacts"][relative] = {"sha256": digest(content), "bytes": len(content)}
    with pytest.raises(ValueError):
        core.load_packet(root, receipt, "holdout")


@pytest.mark.parametrize("mismatch", ["parent_sample", "ledger_parent"])
def test_parent_linkage_is_bound_to_verified_triage(tmp_path, monkeypatch, receipt, mismatch):
    monkeypatch.setattr(core, "check_triage", lambda root: {"sample_sha256": "d" * 64})
    monkeypatch.setattr(core, "implementation_hashes", lambda root: {})
    monkeypatch.setattr(core, "render_report", lambda value: b"synthetic report\n")
    (tmp_path / "reports").mkdir()
    (tmp_path / "reports/triage-audit.json").write_bytes(b"parent")
    receipt["parent_receipt_sha256"] = digest(b"parent")
    receipt["ledger"]["parent_triage_receipt_sha256"] = receipt["parent_receipt_sha256"]
    if mismatch == "parent_sample":
        receipt["parent_sample_sha256"] = "e" * 64
    else:
        receipt["ledger"]["parent_triage_receipt_sha256"] = "e" * 64
    (tmp_path / core.RECEIPT).write_bytes(json_bytes(receipt))
    (tmp_path / core.REPORT).write_bytes(b"synthetic report\n")
    with pytest.raises(ValueError):
        core.check(tmp_path)
