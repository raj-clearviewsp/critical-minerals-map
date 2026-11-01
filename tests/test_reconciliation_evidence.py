"""Source research can prompt adjudication but never becomes a human decision."""

import copy

import pytest

from pipeline.reconciliation.evidence import reconcile_evidence


@pytest.fixture
def evidence_inputs():
    records = {
        "source:a": {"display_name": "Lein 2 Smith Meyers Mine"},
        "source:b": {"display_name": "Nonaas Grube"},
        "source:c": {"display_name": "Bear Valley"},
        "source:d": {"display_name": "Bruce Meadow Placer"},
    }
    pairs = {
        "pair_a": {"left_record_id": "source:a", "right_record_id": "source:b"},
        "pair_b": {"left_record_id": "source:c", "right_record_id": "source:d"},
    }
    latest = [
        {
            "pair_id": identity,
            **pair,
            "label": "same_site",
            "latest_event_id": f"event_{identity}",
            "reviewer": "Original reviewer",
            "notes": 'Original "quoted" notes\nwith trailing space ',
            "history_event_ids": [f"earlier_{identity}", f"event_{identity}"],
        }
        for identity, pair in pairs.items()
    ]
    research = {
        "schema_version": "source-research-v1",
        "research_date": "2026-10-31",
        "cases": [
            {
                "pair_id": "pair_a",
                "names": ["Lein 2 Smith Meyers Mine", "Nonaas Grube"],
                "findings": [
                    {
                        "claim": "Publisher distinguishes Lien II from Nonås.",
                        "url": "https://example.org/report?section=mines#history",
                        "title": "Publisher mine history",
                        "source_date": "1982",
                        "source_kind": "geological_survey_publication",
                        "locator": "Mine histories",
                    }
                ],
                "recommendation": "different_sites",
                "confidence_reason": "Exact alias established; old coordinates differ.",
                "unresolved_questions": ["Original coordinates need verification."],
            }
        ],
    }
    return research, latest, records, pairs


def test_conflicting_research_preserves_human_decision_and_all_inputs(evidence_inputs):
    research, latest, records, pairs = evidence_inputs
    before = copy.deepcopy(evidence_inputs)
    result, summary = reconcile_evidence(*evidence_inputs)
    case = result["cases"][0]
    assert case["latest_human_label"] == "same_site"
    assert case["latest_event_id"] == "event_pair_a"
    assert case["recommendation"] == "different_sites"
    assert case["disposition"] == "human_adjudication"
    assert case["recommendation_status"] == "agent_research_proposal_not_a_human_decision"
    assert case["human_label_changed"] is False
    assert summary["human_labels_changed"] == 0
    assert (research, latest, records, pairs) == before
    assert reconcile_evidence(*evidence_inputs) == (result, summary)

    # Downstream display edits must not mutate the original research or human ledger.
    case["names"][0] = "Lien II"
    case["findings"][0]["claim"] = "Changed display text"
    case["unresolved_questions"].append("Another question")
    assert evidence_inputs == before


@pytest.mark.parametrize(
    "human,proposal,disposition",
    [
        ("same_site", "same_site", "corroborated"),
        ("different_sites", "different_sites", "corroborated"),
        ("left_contains_right", "left_contains_right", "corroborated"),
        ("right_contains_left", "right_contains_left", "corroborated"),
        ("same_site", "different_sites", "human_adjudication"),
        ("same_site", "left_contains_right", "human_adjudication"),
        ("left_contains_right", "right_contains_left", "human_adjudication"),
        ("same_site", "insufficient_evidence", "human_adjudication"),
        ("different_sites", "related_scope_unclear", "human_adjudication"),
        ("uncertain", "left_contains_right", "human_adjudication"),
        ("insufficient_evidence", "different_sites", "human_adjudication"),
        ("related_scope_unclear", "same_site", "human_adjudication"),
        ("uncertain", "insufficient_evidence", "research_open"),
        ("uncertain", "related_scope_unclear", "research_open"),
        ("insufficient_evidence", "insufficient_evidence", "research_open"),
        ("related_scope_unclear", "related_scope_unclear", "research_open"),
        ("insufficient_evidence", "related_scope_unclear", "research_open"),
        ("related_scope_unclear", "insufficient_evidence", "research_open"),
    ],
)
def test_disposition_distinguishes_resolution_from_corroboration(
    evidence_inputs, human, proposal, disposition
):
    research, latest, *_ = evidence_inputs
    latest[0]["label"] = human
    research["cases"][0]["recommendation"] = proposal
    result, summary = reconcile_evidence(*evidence_inputs)
    case = result["cases"][0]
    assert case["disposition"] == disposition
    assert case["latest_human_label"] == human
    assert latest[0]["label"] == human
    assert summary["disposition_counts"] == {disposition: 1}
    assert summary["proposal_counts"] == {proposal: 1}
    assert summary["human_labels_changed"] == 0


def test_case_metadata_cannot_override_the_authoritative_human_label(evidence_inputs):
    case = evidence_inputs[0]["cases"][0]
    case.update(
        {
            "latest_human_label": "different_sites",
            "latest_event_id": "invented_event",
            "disposition": "corroborated",
            "recommendation_status": "accepted_human_decision",
            "human_label_changed": True,
        }
    )
    result, _ = reconcile_evidence(*evidence_inputs)
    actual = result["cases"][0]
    assert actual["latest_human_label"] == "same_site"
    assert actual["latest_event_id"] == "event_pair_a"
    assert actual["disposition"] == "human_adjudication"
    assert actual["recommendation_status"] == "agent_research_proposal_not_a_human_decision"
    assert actual["human_label_changed"] is False


@pytest.mark.parametrize("change", ["unknown", "unreviewed", "absent_pair", "duplicate"])
def test_research_requires_one_known_previously_reviewed_pair(evidence_inputs, change):
    research, latest, _, pairs = evidence_inputs
    if change == "unknown":
        research["cases"][0]["pair_id"] = "pair_unknown"
    elif change == "unreviewed":
        latest.pop(0)
    elif change == "absent_pair":
        pairs.pop("pair_a")
    else:
        research["cases"].append(copy.deepcopy(research["cases"][0]))
    with pytest.raises(ValueError, match="unique previously reviewed pair"):
        reconcile_evidence(*evidence_inputs)


@pytest.mark.parametrize("change", ["reverse", "alias", "wrong_pair", "missing_name"])
def test_names_must_match_exact_records_in_pair_direction(evidence_inputs, change):
    case = evidence_inputs[0]["cases"][0]
    if change == "reverse":
        case["names"].reverse()
    elif change == "alias":
        case["names"][0] = "Lien II (Smith Meyers)"
    elif change == "wrong_pair":
        case["pair_id"] = "pair_b"
    else:
        case.pop("names")
    with pytest.raises(ValueError, match="names do not match"):
        reconcile_evidence(*evidence_inputs)


def test_case_order_is_stable_and_summary_counts_only_researched_cases(evidence_inputs):
    research, latest, *_ = evidence_inputs
    second = copy.deepcopy(research["cases"][0])
    second.update(
        {
            "pair_id": "pair_b",
            "names": ["Bear Valley", "Bruce Meadow Placer"],
            "recommendation": "left_contains_right",
        }
    )
    latest[1]["label"] = "left_contains_right"
    research["cases"].insert(0, second)
    result, summary = reconcile_evidence(*evidence_inputs)
    assert [case["pair_id"] for case in result["cases"]] == ["pair_a", "pair_b"]
    assert summary["researched_cases"] == 2
    assert summary["findings"] == 2
    assert summary["disposition_counts"] == {"human_adjudication": 1, "corroborated": 1}
    assert summary["proposal_counts"] == {"different_sites": 1, "left_contains_right": 1}
    assert result["research_date"] == "2026-10-31"


@pytest.mark.parametrize(
    "url",
    [
        "",
        "http://example.org/report",
        "file:///private/report.pdf",
        "//example.org/report",
        "https:///report",
        "https://user@example.org/report",
        "https://user:password@example.org/report",
        "https://:password@example.org/report",
        "https://@example.org/report",
        "https://:@example.org/report",
        "https://:443/report",
    ],
)
def test_citations_require_https_host_without_credentials(evidence_inputs, url):
    evidence_inputs[0]["cases"][0]["findings"][0]["url"] = url
    with pytest.raises(ValueError, match="citations require HTTPS"):
        reconcile_evidence(*evidence_inputs)


@pytest.mark.parametrize("field", ["url", "title", "claim"])
@pytest.mark.parametrize("change", ["missing", "empty"])
def test_citation_requires_a_source_title_and_claim(evidence_inputs, field, change):
    finding = evidence_inputs[0]["cases"][0]["findings"][0]
    if change == "missing":
        finding.pop(field)
    else:
        finding[field] = ""
    with pytest.raises(ValueError, match="citations require HTTPS"):
        reconcile_evidence(*evidence_inputs)


@pytest.mark.parametrize("findings", [None, []])
def test_empty_citations_are_rejected(evidence_inputs, findings):
    evidence_inputs[0]["cases"][0]["findings"] = findings
    with pytest.raises(ValueError, match="at least one cited finding"):
        reconcile_evidence(*evidence_inputs)


@pytest.mark.parametrize("proposal", [None, "", "uncertain", "likely_match", "unknown"])
def test_proposal_must_be_an_explicit_supported_research_relationship(evidence_inputs, proposal):
    evidence_inputs[0]["cases"][0]["recommendation"] = proposal
    with pytest.raises(ValueError, match="supported proposal"):
        reconcile_evidence(*evidence_inputs)


@pytest.mark.parametrize("confidence_reason", [None, ""])
def test_proposal_requires_its_reason_and_limitations(evidence_inputs, confidence_reason):
    evidence_inputs[0]["cases"][0]["confidence_reason"] = confidence_reason
    with pytest.raises(ValueError, match="supported proposal"):
        reconcile_evidence(*evidence_inputs)


@pytest.mark.parametrize(
    "research",
    [
        {},
        {"schema_version": "research-evidence-v1", "cases": []},
        {"schema_version": "source-research-v1", "cases": {}},
    ],
)
def test_requires_the_combined_source_research_schema(evidence_inputs, research):
    with pytest.raises(ValueError, match="Invalid source-research document"):
        reconcile_evidence(research, *evidence_inputs[1:])
