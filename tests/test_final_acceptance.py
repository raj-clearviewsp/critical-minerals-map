"""Final approval retains human provenance, formal membership and immutable history."""

import copy
import csv
import io
import json
import shutil
from collections import Counter

import pytest

from pipeline.final_acceptance import core
from pipeline.scaffold import digest, json_bytes
from pipeline.triage.labels import sample_digest


def sample_and_approval():
    labels = (
        ["same_site"] * 16
        + ["different_sites"] * 16
        + ["left_contains_right"] * 6
        + ["right_contains_left"]
        + ["related_scope_unclear"] * 6
        + ["insufficient_evidence"] * 2
    )
    sample = {
        "schema_version": "validation-review-v1",
        "mode": "adjudication",
        "original_split": "holdout",
        "evaluation_role": core.ROLE,
        "research_sha256": digest(b"synthetic final research"),
        "cases": [
            {
                "pair_id": f"new_{i}",
                "left_record_id": f"left_{i}",
                "right_record_id": f"right_{i}",
                "research": {"recommendation": label},
            }
            for i, label in enumerate(labels)
        ],
    }
    sample["sample_sha256"] = sample_digest(sample)
    approval = {
        "schema_version": "explicit-chat-approval-v1",
        "capture_method": core.CAPTURE,
        "batch_id": "final47-v1",
        "sample_sha256": sample["sample_sha256"],
        "research_sha256": sample["research_sha256"],
        "reviewer": core.REVIEWER,
        "approved_on": core.APPROVED_ON,
        "user_message": core.MESSAGE,
        "assistance": core.ASSISTANCE,
        "approved_labels": {c["pair_id"]: c["research"]["recommendation"] for c in sample["cases"]},
    }
    return sample, approval


def old_history():
    labels = (
        ["same_site"] * 42
        + ["different_sites"] * 39
        + ["left_contains_right"] * 4
        + ["right_contains_left"] * 4
        + ["insufficient_evidence"] * 7
        + ["related_scope_unclear"] * 56
        + ["uncertain"] * 2
    )
    events = []
    for index, i in enumerate([*range(13), *range(154)]):
        blind = i >= 121
        events.append(
            {
                "event_id": f"old_event_{index}",
                "pair_id": f"old_{i}",
                "left_record_id": f"old_left_{i}",
                "right_record_id": f"old_right_{i}",
                "label": labels[i],
                "reviewer": "Raj Sawhney",
                "notes": f'Exact original note {index}, "quoted"\nsecond line',
                "sample_sha256": "c" * 64,
                "round_id": "next60-completed-v1-holdout" if blind else "older_round",
                "source_csv_sha256": "d" * 64,
                "assistance": "blind_source_only" if blind else "earlier_review",
                "capture_method": "user_submitted_browser_export",
            }
        )
    return {
        "schema_version": "decision-history-v1",
        "events": events,
        "rounds": [
            {"round_id": "older_round", "event_count": 134},
            {"round_id": "next60-completed-v1-holdout", "event_count": 33},
        ],
        "policy": "Preserve every original field and every earlier answer.",
    }


def rows_for(approval, sample):
    return list(csv.DictReader(io.StringIO(core.completed_csv(approval, sample).decode())))


def test_approval_preserves_all_final_labels_as_administrative_transcription():
    sample, approval = sample_and_approval()
    core.validate_approval(approval, sample)
    rows = rows_for(approval, sample)
    assert len(rows) == 47
    assert {r["pair_id"]: r["label"] for r in rows} == approval["approved_labels"]
    assert sum(r["label"] in core.UNRESOLVED for r in rows) == 8
    assert all("Administrative transcription" in r["notes"] for r in rows)
    assert all(r["sample_sha256"] == sample["sample_sha256"] for r in rows)


@pytest.mark.parametrize(
    "field,value",
    [
        ("sample_sha256", "a" * 64),
        ("research_sha256", "a" * 64),
        ("user_message", "I approve a different batch."),
        ("reviewer", "Another reviewer"),
        ("approved_on", "2026-02-30"),
        ("approved_on", "2026-11-03"),
        ("approved_on", "20261104"),
        ("approved_on", None),
        ("assistance", "blind"),
        ("batch_id", "development60-v1"),
        ("capture_method", "automatic_agent_approval"),
        ("approved_labels", {}),
    ],
)
def test_approval_rejects_changed_binding_or_provenance(field, value):
    sample, approval = sample_and_approval()
    approval[field] = value
    with pytest.raises(ValueError):
        core.validate_approval(approval, sample)


@pytest.mark.parametrize(
    "mutation", ["changed_label", "unknown_label", "duplicate", "self", "wrong_role", "digest"]
)
def test_recommendation_and_sample_cannot_be_silently_redefined(mutation):
    sample, approval = sample_and_approval()
    if mutation == "changed_label":
        approval["approved_labels"]["new_0"] = "different_sites"
    elif mutation == "unknown_label":
        sample["cases"][0]["research"]["recommendation"] = "merge_everything"
        approval["approved_labels"]["new_0"] = "merge_everything"
    elif mutation == "duplicate":
        sample["cases"][1] = copy.deepcopy(sample["cases"][0])
    elif mutation == "self":
        sample["cases"][0]["right_record_id"] = "left_0"
    elif mutation == "wrong_role":
        sample["evaluation_role"] = "blind"
    if mutation != "digest":
        sample["sample_sha256"] = sample_digest(sample)
        approval["sample_sha256"] = sample["sample_sha256"]
    else:
        sample["cases"][0]["left_record_id"] = "different record"
    with pytest.raises(ValueError):
        core.validate_approval(approval, sample)


def test_append_preserves_every_prior_event_round_and_blind_collection():
    sample, approval = sample_and_approval()
    history = old_history()
    before = copy.deepcopy(history)
    updated, latest = core.append_approval(
        history, rows_for(approval, sample), approval, sample, "a" * 64, "b" * 64
    )
    assert history == before
    assert updated["events"][:167] == before["events"]
    assert updated["rounds"][:-1] == before["rounds"]
    assert updated["policy"] == before["policy"]
    assert len(updated["events"]) == 214 and len(latest["decisions"]) == 201
    assert latest["counts"]["same_site"] == 58
    assert latest["counts"]["related_scope_unclear"] == 62
    assert latest["history_sha256"] == digest(json_bytes(updated))
    assert all(
        e["evaluation_role"] == core.ROLE
        and e["assistance"] == core.ASSISTANCE
        and e["approval_sha256"] == "b" * 64
        for e in updated["events"][167:]
    )
    assert [e for e in updated["events"] if e.get("assistance") == "blind_source_only"] == [
        e for e in before["events"] if e.get("assistance") == "blind_source_only"
    ]


@pytest.mark.parametrize(
    "mutation",
    [
        "existing_pair",
        "repeated_round",
        "unknown_old_label",
        "row_label",
        "row_note",
        "row_orientation",
    ],
)
def test_append_rejects_history_and_transcription_corruption(mutation):
    sample, approval = sample_and_approval()
    history, rows = old_history(), rows_for(approval, sample)
    if mutation == "existing_pair":
        history["events"][-1]["pair_id"] = "new_0"
    elif mutation == "repeated_round":
        history["rounds"].append({"round_id": core.ROUND})
    elif mutation == "unknown_old_label":
        history["events"][0]["label"] = "unknown"
    elif mutation == "row_label":
        rows[0]["label"] = "different_sites"
    elif mutation == "row_note":
        rows[0]["notes"] = "Invented human note"
    else:
        rows[0]["left_record_id"], rows[0]["right_record_id"] = (
            rows[0]["right_record_id"],
            rows[0]["left_record_id"],
        )
    with pytest.raises(ValueError):
        core.append_approval(history, rows, approval, sample, "a" * 64, "b" * 64)


@pytest.fixture
def packet(tmp_path, monkeypatch):
    root = tmp_path / "atlas"
    sample, approval = sample_and_approval()
    history = old_history()
    latest = core.latest_view(history)
    benchmark = {
        "schema_version": "validation-design-v1",
        "cases": [
            {
                "pair_id": f"old_{i}",
                "left_record_id": f"old_left_{i}",
                "right_record_id": f"old_right_{i}",
                "split": "development" if i <= 120 else "holdout",
            }
            for i in range(1, 154)
        ]
        + [{**{k: c[k] for k in core.HEADERS[:3]}, "split": "holdout"} for c in sample["cases"]],
    }
    benchmark["sample_sha256"] = sample_digest(benchmark)
    inputs = {
        core.PARENT_HISTORY: json_bytes(history),
        core.PARENT_LATEST: json_bytes(latest),
        core.BENCHMARK: json_bytes(benchmark),
        f"{core.RESEARCH_DIRECTORY}/sample.json": json_bytes(sample),
        f"{core.RESEARCH_DIRECTORY}/research-input.json": b"synthetic final research",
    }
    artifacts = {n: {"sha256": digest(v), "bytes": len(v)} for n, v in inputs.items()}
    parent = {
        "sample_sha256": sample["sample_sha256"],
        "artifacts": artifacts,
        "research": {"recommendation_counts": dict(Counter(approval["approved_labels"].values()))},
    }
    previous = {"artifacts": artifacts, "review": {"latest_counts": latest["counts"]}}
    design = {
        "artifacts": artifacts,
        "benchmark_sha256": benchmark["sample_sha256"],
        "selection": {"size": 200},
    }
    inputs.update(
        {
            core.RESEARCH_RECEIPT: json_bytes(parent),
            core.PREVIOUS_RECEIPT: json_bytes(previous),
            core.BENCHMARK_RECEIPT: json_bytes(design),
            core.EXPERIMENT_RECEIPT: b'{"frozen":"unchanged"}\n',
        }
    )
    for name, content in inputs.items():
        p = root / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(content)
    approval_path = tmp_path / "approval.json"
    approval_path.write_bytes(json_bytes(approval))
    monkeypatch.setattr(core, "check_research", lambda *a, **kw: parent)
    monkeypatch.setattr(core, "implementation_hashes", lambda r: {"synthetic": "f" * 64})
    return root, approval_path, parent, previous, design


def test_acceptance_computes_formal_membership_and_is_immutable(packet):
    root, path, *_ = packet
    parents = {p: p.read_bytes() for p in root.rglob("*") if p.is_file()}
    receipt = core.prepare(root, path)
    r = receipt["review"]
    assert r["formal_human_reviewed"] == 200 and r["unique_comparisons"] == 201
    assert r["formal_counts"]["same_site"] == 57
    assert (r["formal_binary_labeled"], r["formal_containment"], r["formal_unresolved"]) == (
        112,
        15,
        73,
    )
    assert r["nonformal_latest_counts"] == {"same_site": 1}
    assert r["blind_collection_cases_preserved"] == 33 and r["new_blind_cases"] == 0
    assert receipt["formal_review_complete"] is True
    assert receipt["formal_binary_gold_complete"] is False
    paths = [root / n for n in (*receipt["artifacts"], core.RECEIPT)]
    before = {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in paths}
    assert core.prepare(root) == receipt and core.check(root, local=True) == receipt
    assert before == {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in paths}
    assert parents == {p: p.read_bytes() for p in parents}
    changed = root / core.DIRECTORY / "completed.csv"
    changed.write_text("do not overwrite")
    with pytest.raises(ValueError):
        core.prepare(root)
    assert changed.read_text() == "do not overwrite"


def test_offline_check_needs_no_private_answers(packet):
    root, path, *_ = packet
    receipt = core.prepare(root, path)
    shutil.rmtree(root / "data/review")
    assert core.check(root) == receipt


@pytest.mark.parametrize(
    "filename",
    [
        core.PARENT_HISTORY,
        core.PARENT_LATEST,
        core.BENCHMARK,
        f"{core.RESEARCH_DIRECTORY}/sample.json",
    ],
)
def test_changed_private_parent_is_rejected(packet, filename):
    root, path, *_ = packet
    (root / filename).write_text("{}")
    with pytest.raises(ValueError):
        core.build(root, path)


@pytest.mark.parametrize("mutation", ["missing", "orientation", "selection", "blind_provenance"])
def test_formal_summary_requires_exact_membership_and_blind_history(packet, mutation):
    root, _, *_ = packet
    benchmark = json.loads((root / core.BENCHMARK).read_text())
    sample = json.loads((root / core.RESEARCH_DIRECTORY / "sample.json").read_text())
    _, approval = sample_and_approval()
    history, latest = core.append_approval(
        old_history(), rows_for(approval, sample), approval, sample, "a" * 64, "b" * 64
    )
    if mutation == "missing":
        latest["decisions"] = [r for r in latest["decisions"] if r["pair_id"] != "old_1"]
    elif mutation == "orientation":
        benchmark["cases"][0]["left_record_id"] = "another"
    elif mutation == "selection":
        sample["cases"].reverse()
    else:
        next(e for e in history["events"] if e["round_id"] == "next60-completed-v1-holdout")[
            "assistance"
        ] = "agent_research"
    benchmark["sample_sha256"] = sample_digest(benchmark)
    with pytest.raises(ValueError):
        core.formal_summary(benchmark, latest, sample, history)


@pytest.mark.parametrize(
    "keys,value",
    [
        (("status",), "pending_human_approval"),
        (("approved_on",), "2026-02-30"),
        (("approval", "reviewer"), "Agent"),
        (("approval", "user_message_sha256"), "a" * 64),
        (("approval", "assistance"), "blind"),
        (("approval", "approved_labels_sha256"), "bad"),
        (("formal_binary_gold_complete",), True),
        (("formal_review_complete",), False),
        (("auto_merges",), False),
        (("review", "new_blind_cases"), 47),
        (("review", "history_events"), 214.0),
        (("review", "formal_human_reviewed"), 201),
        (("review", "latest_unresolved"), 0),
        (("review", "formal_unresolved"), 0),
        (("review", "formal_counts"), {"same_site": 200}),
        (("review", "approved_counts"), {"unknown": 47}),
        (("review", "latest_counts"), {"same_site": 202, "different_sites": -1}),
        (("sample_sha256",), "a" * 64),
        (("research_sha256",), "a" * 64),
        (("benchmark_sha256",), "a" * 64),
        (("approval_sha256",), "a" * 64),
        (("implementation", "synthetic"), "bad"),
    ],
)
def test_receipt_rejects_approval_count_and_checksum_tampering(packet, keys, value):
    root, path, parent, previous, design = packet
    _, receipt = core.build(root, path)
    target = receipt
    for key in keys[:-1]:
        target = target[key]
    target[keys[-1]] = value
    with pytest.raises(ValueError):
        core.validate_receipt(receipt, parent, previous, design)


@pytest.mark.parametrize(
    "field",
    [
        "parent_receipt_sha256",
        "previous_receipt_sha256",
        "benchmark_receipt_sha256",
        "frozen_experiment_receipt_sha256",
    ],
)
def test_offline_check_binds_all_parents_and_frozen_experiment(packet, field):
    root, path, *_ = packet
    receipt = core.prepare(root, path)
    receipt[field] = "a" * 64
    (root / core.RECEIPT).write_bytes(json_bytes(receipt))
    with pytest.raises(ValueError, match="code, parents, experiment or report"):
        core.check(root)


def test_local_check_rejects_changed_private_approval(packet):
    root, path, *_ = packet
    core.prepare(root, path)
    accepted = root / core.DIRECTORY / "approval.json"
    approval = json.loads(accepted.read_text())
    approval["user_message"] = "An invented approval"
    accepted.write_bytes(json_bytes(approval))
    with pytest.raises(ValueError, match="exact chat statement"):
        core.check(root, local=True)
