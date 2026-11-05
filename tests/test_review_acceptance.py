"""Explicit approval binds the final recommendations and preserves earlier decisions."""

import copy
import csv
import io

import pytest

from pipeline.review_acceptance import core
from pipeline.scaffold import digest, json_bytes
from pipeline.triage.labels import sample_digest


def sample_and_approval():
    cases = [
        {
            "pair_id": f"new_{i}",
            "left_record_id": f"left_{i}",
            "right_record_id": f"right_{i}",
            "research": {"recommendation": "related_scope_unclear" if i < 26 else "same_site"},
        }
        for i in range(60)
    ]
    sample = {
        "schema_version": "validation-review-v1",
        "mode": "development",
        "research_sha256": "a" * 64,
        "cases": cases,
    }
    sample["sample_sha256"] = sample_digest(sample)
    approval = {
        "schema_version": "explicit-chat-approval-v1",
        "capture_method": "explicit_user_chat_approval",
        "batch_id": "development60-v1",
        "sample_sha256": sample["sample_sha256"],
        "research_sha256": sample["research_sha256"],
        "reviewer": "Raj Sawhney",
        "approved_on": "2026-11-04",
        "user_message": "I reviewed these 60 and agree with all findings. Accept them all.",
        "assistance": "agent_research_and_second_review",
        "approved_labels": {c["pair_id"]: c["research"]["recommendation"] for c in cases},
    }
    return sample, approval


def test_approval_binds_final_labels_and_preserves_unresolved():
    sample, approval = sample_and_approval()
    core.validate_approval(approval, sample)
    content = core.completed_csv(approval, sample)
    rows = list(csv.DictReader(io.StringIO(content.decode())))
    assert len(rows) == 60
    assert sum(row["label"] == "related_scope_unclear" for row in rows) == 26
    assert all(row["sample_sha256"] == sample["sample_sha256"] for row in rows)
    assert all("Administrative transcription" in row["notes"] for row in rows)


@pytest.mark.parametrize(
    "field,value",
    [
        ("sample_sha256", "b" * 64),
        ("research_sha256", "b" * 64),
        ("reviewer", " "),
        ("user_message", ""),
        ("approved_labels", {}),
        ("capture_method", "automatic_agent_approval"),
        ("assistance", "blind"),
    ],
)
def test_unbound_or_misattributed_approval_rejected(field, value):
    sample, approval = sample_and_approval()
    approval[field] = value
    with pytest.raises(ValueError, match="explicitly bind"):
        core.validate_approval(approval, sample)


def test_changed_recommendation_cannot_be_silently_approved():
    sample, approval = sample_and_approval()
    approval["approved_labels"]["new_0"] = "same_site"
    with pytest.raises(ValueError):
        core.validate_approval(approval, sample)


def old_history():
    events = []
    for i in range(47):
        index = i % 34
        events.append(
            {
                "event_id": f"old_event_{i}",
                "pair_id": f"old_{index}",
                "left_record_id": f"old_left_{index}",
                "right_record_id": f"old_right_{index}",
                "label": "insufficient_evidence" if index < 7 else "different_sites",
                "reviewer": "Raj Sawhney",
                "notes": f"Exact original note {i}",
                "sample_sha256": "c" * 64,
                "round_id": "old_round",
                "source_csv_sha256": "d" * 64,
            }
        )
    return {
        "schema_version": "decision-history-v1",
        "rounds": [{"round_id": "old_round", "event_count": 47}],
        "events": events,
        "policy": "Preserve human decisions.",
    }


def test_append_retains_all_prior_events_and_provenance():
    sample, approval = sample_and_approval()
    history = old_history()
    snapshot = copy.deepcopy(history)
    content = core.completed_csv(approval, sample)
    rows = list(csv.DictReader(io.StringIO(content.decode())))
    updated, latest = core.append_approval(history, rows, approval, digest(content), "e" * 64)
    assert history == snapshot
    assert updated["events"][:47] == snapshot["events"]
    assert updated["rounds"][:-1] == snapshot["rounds"]
    assert len(updated["events"]) == 107
    assert len(latest["decisions"]) == 94
    assert latest["counts"]["related_scope_unclear"] == 26
    assert latest["counts"]["insufficient_evidence"] == 7
    assert latest["history_sha256"] == digest(json_bytes(updated))
    assert all(event["approval_sha256"] == "e" * 64 for event in updated["events"][47:])


def test_prior_pair_or_duplicate_round_cannot_be_appended_again():
    sample, approval = sample_and_approval()
    rows = list(csv.DictReader(io.StringIO(core.completed_csv(approval, sample).decode())))
    history, _ = core.append_approval(old_history(), rows, approval, "a" * 64, "b" * 64)
    with pytest.raises(ValueError, match="pre-approval"):
        core.append_approval(history, rows, approval, "a" * 64, "b" * 64)
    history = old_history()
    history["events"][0]["pair_id"] = "new_0"
    with pytest.raises(ValueError, match="unique new"):
        core.append_approval(history, rows, approval, "a" * 64, "b" * 64)


@pytest.fixture
def acceptance_fixture(tmp_path, monkeypatch):
    root = tmp_path / "atlas"
    sample, approval = sample_and_approval()
    previous = {"decisions": [{"pair_id": f"old_{i}"} for i in range(34)]}
    inputs = {
        core.PARENT_HISTORY: json_bytes(old_history()),
        core.PARENT_LATEST: json_bytes(previous),
        f"{core.RESEARCH_DIRECTORY}/sample.json": json_bytes(sample),
    }
    artifacts = {
        name: {"sha256": digest(content), "bytes": len(content)} for name, content in inputs.items()
    }
    parent = {
        "sample_sha256": sample["sample_sha256"],
        "artifacts": artifacts,
        "research": {"recommendation_counts": {"related_scope_unclear": 26, "same_site": 34}},
    }
    inputs[core.RESEARCH_RECEIPT] = json_bytes(parent)
    inputs["reports/private-preview-audit.json"] = json_bytes({"artifacts": artifacts})
    for name, content in inputs.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    approval_path = tmp_path / "approval.json"
    approval_path.write_bytes(json_bytes(approval))
    monkeypatch.setattr(core, "check_research", lambda *args, **kwargs: parent)
    monkeypatch.setattr(core, "implementation_hashes", lambda _: {"test": "a" * 64})
    return root, approval_path


def test_acceptance_is_reproducible_and_never_overwrites(acceptance_fixture):
    root, path = acceptance_fixture
    receipt = core.prepare(root, path)
    artifacts = [root / name for name in receipt["artifacts"]] + [root / core.RECEIPT]
    before = {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in artifacts}
    assert core.prepare(root) == receipt
    assert core.check(root, local=True) == receipt
    assert before == {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in artifacts}
    (root / core.DIRECTORY / "completed.csv").write_text("different content")
    with pytest.raises(ValueError):
        core.prepare(root)
    assert (root / core.DIRECTORY / "completed.csv").read_text() == "different content"


def test_offline_check_does_not_require_private_artifacts(acceptance_fixture):
    root, path = acceptance_fixture
    core.prepare(root, path)
    import shutil

    shutil.rmtree(root / "data/review")
    assert core.check(root)["review"]["new_approved_decisions"] == 60


def test_tampered_parent_history_is_rejected(acceptance_fixture):
    root, path = acceptance_fixture
    (root / core.PARENT_HISTORY).write_text("{}")
    with pytest.raises(ValueError, match="Accepted artifact changed"):
        core.build(root, path)
