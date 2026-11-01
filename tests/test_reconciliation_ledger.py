"""Review imports preserve both rounds and keep diagnostic outcomes separate from truth."""

import copy
import csv
import io
import json

import pytest

from pipeline.link.labels import HEADERS
from pipeline.reconciliation import ledger
from pipeline.scaffold import digest, json_bytes
from pipeline.triage.labels import preserve_import, sample_digest

PRIVATE_REVIEWER = "PRIVATE REVIEWER"
PRIVATE_NOTE = 'Original "quoted", café\nSecond line with trailing space '


def export(rows):
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=HEADERS, lineterminator="\r\n")
    writer.writeheader()
    writer.writerows(rows)
    return b"\xef\xbb\xbf" + stream.getvalue().encode()


def pair(number):
    return {
        "pair_id": f"pair_{number}",
        "left_record_id": f"a:{number}",
        "right_record_id": f"b:{number}",
    }


@pytest.fixture
def checkpoint(tmp_path, monkeypatch):
    original_sample = {"pairs": [pair(i) for i in range(200)], "sample_sha256": "a" * 64}
    original_rows = []
    for i, ids in enumerate(original_sample["pairs"]):
        label = (
            "same_site"
            if i < 11
            else "different_sites"
            if i < 16
            else ("uncertain" if i < 20 else "")
        )
        original_rows.append(
            {
                **ids,
                "label": label,
                "reviewer": PRIVATE_REVIEWER if label else "",
                "notes": PRIVATE_NOTE + str(i) if label else "",
                "sample_sha256": "a" * 64,
            }
        )
    old_path = tmp_path / ledger.ORIGINAL_CSV
    old_path.parent.mkdir(parents=True)
    old_path.write_bytes(export(original_rows))
    _, preserved = preserve_import(old_path, original_sample)
    cases = []
    for i in range(6):
        cases.append(
            {
                **pair(i),
                "queue": "relationship" if i == 0 else "ambiguous",
                "previous_decision": original_rows[i],
            }
        )
    new_queues = (
        ["likely_match"] * 3 + ["likely_distinct"] * 3 + ["relationship"] * 6 + ["ambiguous"] * 2
    )
    cases.extend(
        {**pair(200 + i), "queue": queue, "previous_decision": None}
        for i, queue in enumerate(new_queues)
    )
    sample = {"schema_version": "triage-pilot-v2", "cases": cases}
    sample["sample_sha256"] = sample_digest(sample)
    labels = (
        ["left_contains_right"]
        + ["same_site"] * 5
        + ["same_site"] * 4
        + ["different_sites"] * 2
        + ["same_site"] * 4
        + ["related_scope_unclear", "insufficient_evidence", "same_site", "related_scope_unclear"]
    )
    completed_rows = [
        {
            **{key: case[key] for key in HEADERS[:3]},
            "label": label,
            "reviewer": PRIVATE_REVIEWER,
            "notes": "",
            "sample_sha256": sample["sample_sha256"],
        }
        for case, label in zip(cases, labels, strict=True)
    ]
    completed_rows[1]["notes"] = PRIVATE_NOTE
    csv_path = tmp_path / "completed.csv"
    csv_path.write_bytes(export(completed_rows))
    original_sample_bytes = json_bytes(original_sample)
    original_receipt = {
        "artifacts": {
            ledger.ORIGINAL_SAMPLE: {
                "sha256": digest(original_sample_bytes),
                "bytes": len(original_sample_bytes),
            }
        }
    }
    original_receipt_bytes = json_bytes(original_receipt)
    contents = {
        ledger.PRESERVED: json_bytes(preserved),
        "data/review/triage-v2/pilot.json": json_bytes(sample),
        ledger.ORIGINAL_CSV: old_path.read_bytes(),
        "data/review/triage-v2/other-parent-artifact.txt": b"all artifacts stay unchanged",
    }
    parent = {
        "sample_sha256": sample["sample_sha256"],
        "original_review_audit_sha256": digest(original_receipt_bytes),
        "artifacts": {
            name: {"sha256": digest(value), "bytes": len(value)} for name, value in contents.items()
        },
    }
    contents.update(
        {
            ledger.ORIGINAL_RECEIPT: original_receipt_bytes,
            ledger.ORIGINAL_SAMPLE: original_sample_bytes,
            ledger.TRIAGE_RECEIPT: json_bytes(parent),
        }
    )
    for relative, value in contents.items():
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(value)
    monkeypatch.setattr(ledger, "check_triage", lambda root: parent)
    return tmp_path, csv_path, parent, original_rows, completed_rows, sample


def test_import_preserves_exact_bytes_and_all_original_fields(checkpoint):
    root, csv_path, _, old, new, _ = checkpoint
    before = {
        path: (path.read_bytes(), path.stat().st_mtime_ns)
        for path in root.rglob("*")
        if path.is_file()
    }
    outputs, summary = ledger.build_ledger(root, csv_path)
    assert outputs[f"{ledger.DIRECTORY}/completed-pilot.csv"] == csv_path.read_bytes()
    history = json.loads(outputs[f"{ledger.DIRECTORY}/decision-history.json"])
    expected = [row for row in old if row["label"]] + new
    assert [{key: row[key] for key in HEADERS} for row in history["events"]] == expected
    assert len({event["event_id"] for event in history["events"]}) == 40
    assert summary["history_events"] == 40 and summary["unique_reviewed_pairs"] == 34
    assert summary["pilot_label_counts"] == {
        "same_site": 14,
        "different_sites": 2,
        "left_contains_right": 1,
        "related_scope_unclear": 2,
        "insufficient_evidence": 1,
    }
    assert summary["latest_unresolved"] == 7
    assert summary["rule_disagreements"] == 1
    assert summary["changed_labels"] == 1 and summary["unchanged_repeated_labels"] == 5
    assert ledger.build_ledger(root, csv_path) == (outputs, summary)
    assert before == {
        path: (path.read_bytes(), path.stat().st_mtime_ns)
        for path in root.rglob("*")
        if path.is_file()
    }


def test_latest_is_explicit_derived_view_and_original_history_survives(checkpoint):
    root, csv_path, *_ = checkpoint
    outputs, summary = ledger.build_ledger(root, csv_path)
    history_bytes = outputs[f"{ledger.DIRECTORY}/decision-history.json"]
    history = json.loads(history_bytes)
    latest = json.loads(outputs[f"{ledger.DIRECTORY}/latest-decisions.json"])
    row = next(row for row in latest["decisions"] if row["pair_id"] == "pair_0")
    assert row["label"] == "left_contains_right" and len(row["history_event_ids"]) == 2
    original = next(
        event for event in history["events"] if event["event_id"] == row["history_event_ids"][0]
    )
    assert original["label"] == "same_site" and original["notes"] == PRIVATE_NOTE + "0"
    assert latest["history_sha256"] == digest(history_bytes)
    assert summary["latest_label_counts"] == {
        "same_site": 19,
        "different_sites": 7,
        "left_contains_right": 1,
        "uncertain": 4,
        "related_scope_unclear": 2,
        "insufficient_evidence": 1,
    }
    public = json_bytes(summary).decode()
    assert all(text not in public for text in [PRIVATE_NOTE, PRIVATE_REVIEWER, "pair_0", "a:0"])
    assert summary["formal_gold_set_complete"] is False
    assert summary["accuracy_metrics_reported"] is False
    assert summary["model_fitted"] is False and summary["auto_merges"] == 0


def test_outcomes_keep_rule_diagnostics_distinct_from_human_labels(checkpoint):
    root, csv_path, *_ = checkpoint
    outputs, summary = ledger.build_ledger(root, csv_path)
    outcomes = json.loads(outputs[f"{ledger.DIRECTORY}/pilot-outcomes.json"])
    assert outcomes["rule_disagreement_pair_ids"] == ["pair_203"]
    assert outcomes["unresolved_pair_ids"] == ["pair_210", "pair_211", "pair_213"]
    assert summary["pilot_queue_by_label"] == {
        "likely_match": {"same_site": 3},
        "likely_distinct": {"different_sites": 2, "same_site": 1},
        "relationship": {
            "same_site": 4,
            "left_contains_right": 1,
            "related_scope_unclear": 1,
            "insufficient_evidence": 1,
        },
        "ambiguous": {"same_site": 6, "related_scope_unclear": 1},
    }


@pytest.mark.parametrize(
    "relative",
    [
        ledger.ORIGINAL_CSV,
        ledger.PRESERVED,
        "data/review/triage-v2/pilot.json",
        "data/review/triage-v2/other-parent-artifact.txt",
        ledger.ORIGINAL_SAMPLE,
    ],
)
def test_rejects_changed_parent_artifacts(checkpoint, relative):
    root, csv_path, *_ = checkpoint
    with (root / relative).open("ab") as stream:
        stream.write(b"changed")
    with pytest.raises(ledger.LedgerError, match="artifact changed"):
        ledger.build_ledger(root, csv_path)


@pytest.mark.parametrize(
    "field,value",
    [
        ("sample_sha256", "b" * 64),
        ("left_record_id", "wrong"),
        ("label", "likely_match"),
        ("label", ""),
        ("reviewer", ""),
    ],
)
def test_rejects_invalid_or_incomplete_completed_export(checkpoint, field, value):
    root, csv_path, _, _, rows, _ = checkpoint
    rows[0][field] = value
    csv_path.write_bytes(export(rows))
    with pytest.raises(ValueError):
        ledger.build_ledger(root, csv_path)


def test_rejects_duplicate_csv_row(checkpoint):
    root, csv_path, _, _, rows, _ = checkpoint
    rows[-1] = copy.deepcopy(rows[0])
    csv_path.write_bytes(export(rows))
    with pytest.raises(ValueError, match="duplicate"):
        ledger.build_ledger(root, csv_path)


def test_input_csv_order_does_not_change_decision_order(checkpoint):
    root, csv_path, _, _, rows, _ = checkpoint
    original_outputs, _ = ledger.build_ledger(root, csv_path)
    csv_path.write_bytes(export(list(reversed(rows))))
    outputs, _ = ledger.build_ledger(root, csv_path)
    prior = json.loads(original_outputs[f"{ledger.DIRECTORY}/decision-history.json"])
    after = json.loads(outputs[f"{ledger.DIRECTORY}/decision-history.json"])
    assert [{key: event[key] for key in HEADERS} for event in prior["events"]] == [
        {key: event[key] for key in HEADERS} for event in after["events"]
    ]
    assert outputs[f"{ledger.DIRECTORY}/completed-pilot.csv"] == csv_path.read_bytes()
    assert prior["rounds"][1]["source_csv_sha256"] != after["rounds"][1]["source_csv_sha256"]


def test_detects_csv_changed_during_validation(checkpoint, monkeypatch):
    root, csv_path, *_ = checkpoint
    validate = ledger.validate_pilot_labels

    def changing_validate(*args, **kwargs):
        result = validate(*args, **kwargs)
        csv_path.write_bytes(csv_path.read_bytes() + b"\n")
        return result

    monkeypatch.setattr(ledger, "validate_pilot_labels", changing_validate)
    with pytest.raises(ledger.LedgerError, match="changed during validation"):
        ledger.build_ledger(root, csv_path)


@pytest.mark.parametrize("change", ["reverse", "reuse", "prior", "duplicate"])
def test_rejects_history_identity_or_provenance_conflicts(checkpoint, change):
    _, _, _, old, new, sample = checkpoint
    original = [row for row in old if row["label"]]
    if change == "reverse":
        new[0]["left_record_id"], new[0]["right_record_id"] = (
            new[0]["right_record_id"],
            new[0]["left_record_id"],
        )
    elif change == "reuse":
        new[-1]["left_record_id"], new[-1]["right_record_id"] = (
            new[0]["left_record_id"],
            new[0]["right_record_id"],
        )
    elif change == "prior":
        sample["cases"][0]["previous_decision"] = None
    else:
        original.append(copy.deepcopy(original[0]))
    with pytest.raises(ledger.LedgerError):
        ledger.derive_ledger(
            original,
            new,
            sample["cases"],
            original_csv_sha256="a" * 64,
            completed_csv_sha256="b" * 64,
        )


def test_rejects_parent_receipt_changed_during_check(checkpoint, monkeypatch):
    root, csv_path, parent, *_ = checkpoint

    def changing_check(path):
        (path / ledger.TRIAGE_RECEIPT).write_bytes(b"{}")
        return parent

    monkeypatch.setattr(ledger, "check_triage", changing_check)
    with pytest.raises(ledger.LedgerError, match="receipt changed"):
        ledger.build_ledger(root, csv_path)
