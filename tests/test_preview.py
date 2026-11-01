"""Private preview tests use small synthetic sources, never local acquired archives."""

import copy
import csv
import io
import json

import pytest

from pipeline.link.labels import HEADERS
from pipeline.normalize.core import NormalizeError
from pipeline.preview import core
from pipeline.scaffold import digest, json_bytes
from pipeline.triage.labels import sample_digest


def csv_bytes(rows):
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=HEADERS)
    writer.writeheader()
    writer.writerows(rows)
    return stream.getvalue().encode()


@pytest.fixture
def preview(tmp_path, monkeypatch):
    def write(name, content):
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        return {"sha256": digest(content), "bytes": len(content)}

    rows = [
        {
            "pair_id": f"pair-{index}",
            "left_record_id": f"left-{index}",
            "right_record_id": f"right-{index}",
            "label": "same_site",
            "reviewer": "PRIVATE-REVIEWER",
            "notes": "PRIVATE-REVIEW-NOTES",
            "sample_sha256": "0" * 64,
        }
        for index in range(34)
    ]
    events = []
    for index, row in enumerate([*rows, *rows[:6]]):
        event = {
            **row,
            "round_id": "original-review" if index < 20 else "triage-pilot",
            "source_csv_sha256": "0" * 64,
        }
        event["event_id"] = "decision_" + digest(json_bytes(event))
        events.append(event)
    history = {
        "schema_version": "decision-history-v1",
        "rounds": [
            {"round_id": key, "source_csv_sha256": "0" * 64, "event_count": 20}
            for key in ("original-review", "triage-pilot")
        ],
        "events": events,
        "policy": "Preserve original decisions.",
    }
    cases = [{key: row[key] for key in HEADERS[:3]} for row in rows[:7]]
    packet = {"schema_version": "validation-review-v1", "mode": "adjudication", "cases": cases}
    packet["sample_sha256"] = sample_digest(packet)
    answer_labels = (
        ["different_sites"] * 4 + ["left_contains_right"] + ["related_scope_unclear"] * 2
    )
    answers = [
        {**row, "label": label, "sample_sha256": packet["sample_sha256"]}
        for row, label in zip(rows[:7], answer_labels, strict=True)
    ]
    adjudication = tmp_path / "provided.csv"
    adjudication.write_bytes(csv_bytes(answers))
    records = {}
    for index, row in enumerate(rows):
        for key in ("left_record_id", "right_record_id"):
            record_id = row[key]
            records[record_id] = {
                "record_id": record_id,
                "source_id": "fixture",
                "display_name": f"Source observation {index}",
                "lat": 40.0,
                "lon": -100.0,
                "country": "Must not infer a country from this name",
                "country_iso3s": ["USA"],
                "pilot_families": ["Co", "REE"],
                "entity_level": "unknown",
                "raw_status": {"Status": "Suspended"},
                "identity_aliases": ["Archive alias"],
                "context_names": ["Context district"],
                "site_url": "https://source.example/observation",
                "source_url": "https://source.example/query?" + "x" * 3000,
                "source_notes": "PRIVATE-REVIEW-NOTES",
                "provenance": {
                    "retrieved_at": "2026-10-31T00:00:00Z",
                    "source_currency": "2018",
                    "source_table": "Observations",
                    "source_row": index,
                    "raw_sha256": "0" * 64,
                },
            }
    registry = {
        "source_id": "fixture",
        "name": "Fixture archive",
        "url": "https://source.example/archive",
        "license": "Noncommercial license",
        "redistribution_ok": False,
    }
    write("sources/fixture.yaml", json_bytes(registry))
    triage = {
        "eligible_records": len(records),
        "entity_levels": {"unknown": len(records)},
        "source_view_pairs": 1,
        "artifacts": {
            core.RECORDS: write(core.RECORDS, json_bytes(records)),
            core.VIEWS: write(core.VIEWS, json_bytes({"pairs": ["verified-pair"]})),
        },
    }
    write(core.TRIAGE_RECEIPT, json_bytes(triage))
    parent = {
        "artifacts": {
            core.PARENT_HISTORY: write(core.PARENT_HISTORY, json_bytes(history)),
            "data/review/reconciliation-v1/adjudication.json": write(
                "data/review/reconciliation-v1/adjudication.json", json_bytes(packet)
            ),
        },
        "packets": {"adjudication": {"cases": 7, "sample_sha256": packet["sample_sha256"]}},
        "selection": {"new_units": 167},
        "ledger": {"history_events": 40, "unique_reviewed_pairs": 34},
    }
    write(core.PARENT_RECEIPT, json_bytes(parent))
    monkeypatch.setattr(core, "check_reconciliation", lambda root: parent)
    monkeypatch.setattr(core, "implementation_hashes", lambda root: {"fixture": "0" * 64})
    return tmp_path, adjudication, records, registry, history, answers


def test_import_preserves_exact_seven_and_all_previous_events(preview):
    root, csv_path, records, _registry, old, answers = preview
    outputs, receipt = core.build(root, csv_path)
    assert not (root / core.DATASET).exists(), "Pure build must not accept artifacts"
    assert outputs[core.COMPLETED] == csv_path.read_bytes()
    history = json.loads(outputs[core.HISTORY])
    assert history["events"][:40] == old["events"]
    assert history["rounds"][:2] == old["rounds"]
    assert [{key: row[key] for key in HEADERS} for row in history["events"][40:]] == answers
    assert len(history["events"]) == 47
    latest = json.loads(outputs[core.LATEST])
    assert len(latest["decisions"]) == 34
    assert latest["history_sha256"] == digest(outputs[core.HISTORY])
    assert receipt["review"]["imported_label_counts"] == {
        "different_sites": 4,
        "left_contains_right": 1,
        "related_scope_unclear": 2,
    }
    assert receipt["review"]["history_events"] == 47
    dataset = json.loads(outputs[core.DATASET])
    assert {row["record_id"] for row in dataset["records"]} == set(records)
    assert len(dataset["records"]) == 68  # Same-site answers never merge source observations.
    assert dataset["summary"]["publisher_view_duplicate_pairs_retained"] == 1
    assert dataset["summary"]["identity_merges"] == 0
    assert dataset["data_as_of"] is None
    assert dataset["sources"][0]["redistribution_ok"] is False
    assert dataset["records"][0]["status"] == "Status: Suspended"
    assert dataset["records"][0]["source_date"] == "2018"
    assert dataset["records"][0]["identity_review"] == "Has reviewed comparisons"
    for content in (outputs[core.DATASET], json_bytes(receipt)):
        assert b"PRIVATE-REVIEWER" not in content
        assert b"PRIVATE-REVIEW-NOTES" not in content


def test_immutable_prepare_and_offline_vs_local_check(preview):
    root, csv_path, *_ = preview
    receipt = core.prepare(root, csv_path)
    assert core.prepare(root, csv_path) == receipt
    assert core.check(root, local=True) == receipt
    (root / core.DATASET).write_text("changed")
    assert core.check(root) == receipt  # Offline CI needs no private dataset.
    with pytest.raises(ValueError, match="artifact changed"):
        core.check(root, local=True)
    with pytest.raises(NormalizeError, match="Existing staging artifact differs"):
        core.prepare(root, csv_path)


def test_incomplete_or_changed_parent_review_is_rejected(preview):
    root, csv_path, *_rest, answers = preview
    answers[0]["label"] = ""
    csv_path.write_bytes(csv_bytes(answers))
    with pytest.raises(ValueError, match="required answers"):
        core.build(root, csv_path)
    answers[0]["label"] = "different_sites"
    csv_path.write_bytes(csv_bytes(answers))
    (root / core.PARENT_HISTORY).write_text("{}")
    with pytest.raises(ValueError, match="parent artifact changed"):
        core.build(root, csv_path)


def test_registry_changes_fail_offline_check(preview):
    root, csv_path, *_ = preview
    core.prepare(root, csv_path)
    (root / "sources/fixture.yaml").write_text("{}")
    with pytest.raises(ValueError, match="source registry changed"):
        core.check(root)


@pytest.mark.parametrize("latitude,longitude", [(float("nan"), 1), (91, 1), (1, -181), (True, 1)])
def test_invalid_coordinates_rejected(preview, latitude, longitude):
    _root, _csv, records, registry, *_ = preview
    records["left-0"].update(lat=latitude, lon=longitude)
    with pytest.raises(ValueError, match="coordinates"):
        core.project_dataset(records, {"fixture": registry}, {"decisions": []}, 0)


@pytest.mark.parametrize(
    "url",
    [
        "javascript:alert(1)",
        "data:text/html,test",
        "http://source.example",
        "https://u:p@source.example",
        "https://source.example/\npath",
        "https://source.example/" + "x" * 2048,
        "https://source.example\\evil",
        "https://source.example:bad/path",
    ],
)
def test_unsafe_source_citations_use_registry(preview, url):
    _root, _csv, records, registry, *_ = preview
    records["left-0"]["site_url"] = url
    assert core.safe_url(url) == ""
    result = core.project_dataset(records, {"fixture": registry}, {"decisions": []}, 0)
    row = next(item for item in result["records"] if item["record_id"] == "left-0")
    assert row["source_url"] == registry["url"]
    assert row["identity_review"] == "Identity not consolidated"


@pytest.mark.parametrize("codes", [[], ["USA", "CAN"], ["ZZZ"]])
def test_unknown_or_ambiguous_country_is_not_inferred(preview, codes):
    _root, _csv, records, registry, *_ = preview
    records["left-0"]["country_iso3s"] = codes
    result = core.project_dataset(records, {"fixture": registry}, {"decisions": []}, 0)
    row = next(item for item in result["records"] if item["record_id"] == "left-0")
    assert row["country"] == row["country_name"] == ""
    assert row["aliases"] == ["Archive alias"]
    assert row["context_names"] == ["Context district"]


def test_adjudication_cannot_flip_preserved_comparison(preview):
    _root, _csv, _records, _registry, history, answers = preview
    changed = copy.deepcopy(answers)
    changed[0]["left_record_id"], changed[0]["right_record_id"] = (
        changed[0]["right_record_id"],
        changed[0]["left_record_id"],
    )
    with pytest.raises(ValueError, match="orientation"):
        core.append_adjudication(history, changed, "0" * 64)
