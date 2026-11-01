"""Research stays distinct from human approval and bound to the frozen development sample."""

import copy
import csv
import io
import json
import shutil
import subprocess

import pytest

from pipeline.research_review import core
from pipeline.research_review.review_page import _SCRIPT, render_dossier, render_workspace
from pipeline.scaffold import digest, json_bytes
from pipeline.triage.labels import sample_digest


def make_case(number):
    return {
        "pair_id": f"pair_{number}",
        "left_record_id": f"source_a:{number}",
        "right_record_id": f"source_b:{number}",
        "distance_km": 0.5,
        "shared_families": ["Ni"],
        "neighbors": [],
        "neighbor_count": 0,
        "left": {
            "record_id": f"source_a:{number}",
            "source_id": "source_a",
            "display_name": f"Test ridge {number}",
            "entity_level": "site",
            "lat": 10,
            "lon": 20,
        },
        "right": {
            "record_id": f"source_b:{number}",
            "source_id": "source_b",
            "display_name": f"Test mine {number}",
            "entity_level": "site",
            "lat": 10.01,
            "lon": 20,
        },
    }


def proposal(number):
    return {
        "pair_id": f"pair_{number}",
        "case_number": number,
        "analyst": "first_agent",
        "recommendation": "same_site",
        "initial_recommendation": "same_site",
        "confidence": "medium",
        **{key: f"Detailed {key} for the source identities." for key in core.TEXT_FIELDS},
        "rationale": ["The publisher identifies one deposit.", "The source crosswalk is explicit."],
        "counterarguments": ["An operation could include several deposits."],
        "uncertainties": ["Coordinate accuracy is not stated."],
        "what_would_change": ["A publisher map distinguishing the two assets."],
        "citations": [
            {
                "url": "https://publisher.example/report",
                "title": "Primary report",
                "publisher": "Source publisher",
                "source_date": None,
                "locator": "Table 1",
                "access": "full_text",
                "claim": "The exact identifiers refer to one deposit.",
                "independence_note": "Independent of the secondary compilation.",
            }
        ],
        "second_review": {
            "reviewer": "second_agent",
            "outcome": "agree",
            "note": "The source crosswalk supports the proposal.",
            "verifications": ["Checked both source IDs and the alternate interpretation."],
        },
    }


@pytest.fixture
def research_fixture(tmp_path, monkeypatch):
    root = tmp_path / "atlas"
    (root / "reports").mkdir(parents=True)
    (root / "data/review/adjudication-v1").mkdir(parents=True)
    (root / "data/review/linkage").mkdir(parents=True)
    cases = [make_case(n) for n in range(1, 88)]
    parent = {"schema_version": "validation-review-v1", "mode": "development", "cases": cases}
    parent["sample_sha256"] = sample_digest(parent)
    parent_receipt = {
        "packets": {
            "development": {"sample_sha256": parent["sample_sha256"], "cases": 87},
            "holdout": {"cases": 80},
        }
    }
    (root / "reports/reconciliation-audit.json").write_bytes(json_bytes(parent_receipt))
    (root / "reports/private-preview-audit.json").write_text("{}")
    latest_name = "data/review/adjudication-v1/latest-decisions.json"
    latest = json_bytes({"decisions": [{"pair_id": "old_pair"}]})
    (root / latest_name).write_bytes(latest)
    (root / "data/review/linkage/sample.json").write_text(
        '{"source_names":{"source_a":"Publisher A"}}'
    )
    preview = {
        "artifacts": {latest_name: {"sha256": digest(latest)}},
        "review": {"history_events": 47, "unique_comparisons": 34, "unresolved_comparisons": 7},
    }
    monkeypatch.setattr(core, "check_preview", lambda _: preview)
    monkeypatch.setattr(core, "load_packet", lambda *args: copy.deepcopy(parent))
    monkeypatch.setattr(core, "implementation_hashes", lambda _: {"test": "a" * 64})
    research = {
        "schema_version": "researched-development60-v1",
        "cases": [proposal(n) for n in range(1, 61)],
    }
    path = tmp_path / "research.json"
    path.write_bytes(json_bytes(research))
    return root, path, research, parent, preview


def test_build_preserves_exact_selected_source_records_and_blank_decisions(research_fixture):
    root, path, research, parent, _ = research_fixture
    before = copy.deepcopy(parent)
    outputs, receipt = core.build(root, path)
    sample = json.loads(outputs[f"{core.DIRECTORY}/sample.json"])
    assert len(sample["cases"]) == 60
    for actual, original in zip(sample["cases"], parent["cases"][:60], strict=True):
        assert {k: v for k, v in actual.items() if k != "research"} == original
    assert parent == before
    assert sample_digest(sample) == sample["sample_sha256"]
    assert sample["parent_sample_sha256"] == parent["sample_sha256"]
    assert receipt["review"]["new_human_labels_created"] == 0
    assert receipt["review"]["holdout_cases_untouched"] == 80
    rows = list(
        csv.DictReader(io.StringIO(outputs[f"{core.DIRECTORY}/decisions-blank.csv"].decode()))
    )
    assert len(rows) == 60 and all(
        not r["label"] and not r["reviewer"] and not r["notes"] for r in rows
    )
    assert all(r["sample_sha256"] == sample["sample_sha256"] for r in rows)
    assert b"test ridge" not in json_bytes(receipt).lower()


def test_prepare_rerun_preserves_bytes_and_mtimes(research_fixture):
    root, path, *_ = research_fixture
    receipt = core.prepare(root, path)
    snapshots = {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in root.rglob("*") if p.is_file()}
    assert core.prepare(root) == receipt
    assert core.check(root, local=True) == receipt
    assert snapshots == {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in snapshots}


def test_conflicting_outputs_are_not_overwritten(research_fixture):
    root, path, *_ = research_fixture
    core.prepare(root, path)
    target = root / core.DIRECTORY / "index.html"
    target.write_text("existing user file")
    with pytest.raises(Exception, match="(?i)conflict|existing|differ"):
        core.prepare(root)
    assert target.read_text() == "existing user file"


def test_research_requires_all_exact_ordered_pairs(research_fixture):
    _, _, research, parent, _ = research_fixture
    research["cases"][0], research["cases"][1] = research["cases"][1], research["cases"][0]
    with pytest.raises(ValueError, match="order"):
        core.validate_research(research, parent["cases"][:60])


@pytest.mark.parametrize(
    "change",
    [
        {"recommendation": "merge"},
        {"confidence": "99%"},
        {"summary": ""},
        {"rationale": ["One weak argument"]},
        {"counterarguments": []},
        {"what_would_change": []},
        {"citations": []},
        {"second_review": None},
        {"analyst": ""},
        {"initial_recommendation": None},
        {"initial_recommendation": "different_sites"},
    ],
)
def test_incomplete_or_unreviewed_research_is_rejected(research_fixture, change):
    _, _, research, parent, _ = research_fixture
    research["cases"][0].update(change)
    with pytest.raises(ValueError):
        core.validate_research(research, parent["cases"][:60])


@pytest.mark.parametrize(
    "field,value",
    [
        ("url", "javascript:alert(1)"),
        ("url", "https://u:p@example.org"),
        ("locator", ""),
        ("independence_note", ""),
        ("access", "assumed"),
        ("source_date", 2026),
        ("access", "local_snapshot"),
    ],
)
def test_citation_access_and_provenance_must_be_explicit(research_fixture, field, value):
    _, _, research, parent, _ = research_fixture
    research["cases"][0]["citations"][0][field] = value
    with pytest.raises(ValueError):
        core.validate_research(research, parent["cases"][:60])


def test_same_agent_cannot_self_certify_second_review(research_fixture):
    _, _, research, parent, _ = research_fixture
    research["cases"][0]["second_review"]["reviewer"] = "first_agent"
    with pytest.raises(ValueError, match="separate reviewer"):
        core.validate_research(research, parent["cases"][:60])


def test_already_reviewed_comparison_is_rejected(research_fixture):
    root, path, _, _, preview = research_fixture
    name = "data/review/adjudication-v1/latest-decisions.json"
    content = json_bytes({"decisions": [{"pair_id": "pair_1"}]})
    (root / name).write_bytes(content)
    preview["artifacts"][name]["sha256"] = digest(content)
    with pytest.raises(ValueError, match="already reviewed"):
        core.build(root, path)


def test_modified_latest_answers_cannot_be_used(research_fixture):
    root, path, *_ = research_fixture
    (root / "data/review/adjudication-v1/latest-decisions.json").write_text("{}")
    with pytest.raises(ValueError, match="human decisions changed"):
        core.build(root, path)


def test_complete_export_requires_owner_answers_and_exact_batch(research_fixture, tmp_path):
    root, path, *_ = research_fixture
    core.prepare(root, path)
    blank = root / core.DIRECTORY / "decisions-blank.csv"
    assert core.verify_labels(root, blank)["counts"] == {"blank": 60}
    with pytest.raises(ValueError, match="required answers"):
        core.verify_labels(root, blank, complete=True)
    rows = list(csv.DictReader(io.StringIO(blank.read_text())))
    for row in rows:
        row.update(label="different_sites", reviewer="Synthetic test reviewer")
    export = tmp_path / "completed.csv"

    def write():
        with export.open("w", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)

    write()
    assert core.verify_labels(root, export, complete=True)["all_cases_reviewed"]
    rows[0]["sample_sha256"] = "0" * 64
    write()
    with pytest.raises(ValueError, match="altered|malformed"):
        core.verify_labels(root, export)


def test_recommendations_do_not_initialize_browser_decisions():
    executable = shutil.which("node")
    if not executable:
        pytest.skip("Node unavailable")
    case = make_case(1)
    case["research"] = proposal(1)
    helpers = _SCRIPT.split("/* Browser application. */")[0]
    code = (
        helpers
        + f"\nprocess.stdout.write(JSON.stringify(restoreState({json.dumps([case])},"
        + f"null,'{'a' * 64}')));"
    )
    output = subprocess.run([executable], input=code, text=True, capture_output=True, check=True)
    assert json.loads(output.stdout)["decisions"]["pair_1"] == {"label": "", "notes": ""}


def test_workspace_and_dossier_escape_source_text():
    case = make_case(1)
    case["research"] = proposal(1)
    malicious = '</script><script>alert("x")</script>'
    case["research"]["summary"] = malicious
    page = render_workspace([case], "a" * 64, {}).decode()
    dossier = render_dossier([case], {"citation_count": 1, "independently_reviewed": 1}).decode()
    assert malicious not in page and malicious not in dossier
    assert "\\u003c/script\\u003e" in page
    assert "&lt;/script&gt;" in dossier
    assert "connect-src 'none'" in page
    assert "<script src=" not in page
    assert "atlas-researched-development60-" in page
    assert "Use this recommendation" in page
    assert "#case-1" in dossier
