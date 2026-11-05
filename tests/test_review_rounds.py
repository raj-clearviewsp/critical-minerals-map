import copy

import pytest

from pipeline.review_rounds.core import append_round


def inputs(part="development"):
    history = {
        "schema_version": "decision-history-v1",
        "rounds": [],
        "events": [
            {
                "pair_id": "old",
                "label": "uncertain",
                "left_record_id": "oldA",
                "right_record_id": "oldB",
                "reviewer": "Raj",
                "notes": "preserve me",
                "sample_sha256": "a" * 64,
                "round_id": "previous",
                "event_id": "old_event",
            }
        ],
    }
    rows = [
        {
            "pair_id": f"new{i}",
            "left_record_id": f"a{i}",
            "right_record_id": f"b{i}",
            "label": "related_scope_unclear",
            "reviewer": "Raj",
            "notes": 'A, "note"\nline',
            "sample_sha256": "b" * 64,
        }
        for i in range(27 if part == "development" else 33)
    ]
    return history, rows


@pytest.mark.parametrize("part", ["development", "holdout"])
def test_round_preserves_every_prior_event_and_original_csv_field(part):
    history, rows = inputs(part)
    before = copy.deepcopy(history)
    updated, latest = append_round(history, rows, part, "c" * 64)
    assert history == before
    assert updated["events"][:1] == before["events"]
    for source, event in zip(rows, updated["events"][1:], strict=True):
        assert {key: event[key] for key in source} == source
        assert event["capture_method"] == "user_submitted_browser_export"
        assert event["assistance"] == (
            "blind_source_only" if part == "holdout" else "agent_research_before_human_review"
        )
    assert latest["counts"]["related_scope_unclear"] == len(rows)
    assert len(latest["decisions"]) == len(rows) + 1


@pytest.mark.parametrize("mutation", ["duplicate", "old", "blank", "reviewer", "self", "missing"])
def test_import_rejects_identity_or_completeness_errors(mutation):
    history, rows = inputs()
    if mutation == "duplicate":
        rows[1] = copy.deepcopy(rows[0])
    elif mutation == "old":
        rows[0]["pair_id"] = "old"
    elif mutation == "blank":
        rows[0]["label"] = ""
    elif mutation == "reviewer":
        rows[0]["reviewer"] = " "
    elif mutation == "self":
        rows[0]["right_record_id"] = rows[0]["left_record_id"]
    else:
        rows.pop()
    with pytest.raises(ValueError):
        append_round(history, rows, "development", "c" * 64)


def test_round_cannot_be_appended_twice():
    history, rows = inputs()
    updated, _ = append_round(history, rows, "development", "c" * 64)
    with pytest.raises(ValueError, match="already imported"):
        append_round(updated, rows, "development", "c" * 64)
