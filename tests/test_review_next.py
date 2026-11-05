"""The next batch preserves frozen selection and isolates blind source evidence."""

import copy
import csv
import io
import json
import re
import shutil

import pytest
from test_research_review import make_case, proposal

from pipeline.review_next import core
from pipeline.scaffold import digest, json_bytes
from pipeline.triage.labels import sample_digest


def payload(page):
    return json.loads(
        re.search(rb'<script id="review-data" type="application/json">(.*?)</script>', page)[1]
    )


@pytest.fixture
def next_fixture(tmp_path, monkeypatch):
    root = tmp_path / "atlas"
    parents = {}
    for part, numbers in (("development", range(1, 88)), ("holdout", range(1001, 1081))):
        sample = {
            "schema_version": "validation-review-v1",
            "mode": part,
            "cases": [make_case(n) for n in numbers],
        }
        sample["sample_sha256"] = sample_digest(sample)
        parents[part] = sample
    parent_receipt = {
        "packets": {
            part: {"cases": len(s["cases"]), "sample_sha256": s["sample_sha256"]}
            for part, s in parents.items()
        }
    }
    latest_path = f"{core.ACCEPTED_DIRECTORY}/latest-decisions.json"
    latest = json_bytes({"decisions": [{"pair_id": f"pair_{n}"} for n in range(1, 61)]})
    accepted = {"artifacts": {latest_path: {"sha256": digest(latest), "bytes": len(latest)}}}
    inputs = {
        "reports/reconciliation-audit.json": json_bytes(parent_receipt),
        core.ACCEPTED_RECEIPT: json_bytes(accepted),
        latest_path: latest,
        "data/review/linkage/sample.json": json_bytes(
            {"source_names": {"source_a": "Publisher A"}}
        ),
    }
    for name, content in inputs.items():
        p = root / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(content)
    cases = []
    for number, original_number in enumerate(range(61, 88), 1):
        case = proposal(original_number)
        case["case_number"] = number
        cases.append(case)
    research = {"schema_version": "researched-development27-v1", "cases": cases}
    research_path = tmp_path / "research.json"
    research_path.write_bytes(json_bytes(research))
    monkeypatch.setattr(core, "check_acceptance", lambda *args, **kwargs: accepted)
    monkeypatch.setattr(
        core, "load_packet", lambda root, receipt, mode: copy.deepcopy(parents[mode])
    )
    monkeypatch.setattr(core, "implementation_hashes", lambda _: {"test": "a" * 64})
    return root, research_path, research, parents


def test_exact_selection_source_preservation_and_blind_isolation(next_fixture):
    root, path, _, parents = next_fixture
    outputs, receipt = core.build(root, path)
    dev = json.loads(outputs[f"{core.DIRECTORY}/development.json"])
    blind = json.loads(outputs[f"{core.DIRECTORY}/holdout.json"])
    assert [{k: v for k, v in c.items() if k != "research"} for c in dev["cases"]] == parents[
        "development"
    ]["cases"][60:]
    assert blind["cases"] == parents["holdout"]["cases"][:33]
    assert len(dev["cases"]) + len(blind["cases"]) == 60
    assert receipt["unpresented_holdout"] == 47
    for part in core.PARTS:
        rows = list(
            csv.DictReader(io.StringIO(outputs[f"{core.DIRECTORY}/{part}-blank.csv"].decode()))
        )
        assert all(not row["label"] and not row["reviewer"] and not row["notes"] for row in rows)
    visible = payload(outputs[f"{core.DIRECTORY}/holdout.html"])
    # The renderer adds A/B/neighbor map labels; these are geographic marker labels,
    # not decision labels. The complete original source payload stays blind.
    core.ensure_blind(
        [
            {key: value for key, value in case.items() if key != "review_map"}
            for case in visible["cases"]
        ]
    )
    assert all("research" not in c for c in visible["cases"])
    assert b"Use this recommendation" not in outputs[f"{core.DIRECTORY}/holdout.html"]
    assert b"Detailed summary for" not in outputs[f"{core.DIRECTORY}/holdout.html"]
    assert b"next60:development:v1:" in outputs[f"{core.DIRECTORY}/development.html"]
    assert b"next60:blind:v1:" in outputs[f"{core.DIRECTORY}/holdout.html"]
    assert b"first 60 new development" not in outputs[f"{core.DIRECTORY}/development.html"]
    assert b"all 60 analyses" not in outputs[f"{core.DIRECTORY}/development.html"]
    assert outputs[f"{core.DIRECTORY}/analysis.html"].count(b'<article id="case-') == 27
    assert b'href="development.html#case-27"' in outputs[f"{core.DIRECTORY}/analysis.html"]
    for part in core.PARTS:
        page = outputs[f"{core.DIRECTORY}/{part}.html"]
        assert b"scope documented by the source records" in page
        assert b"one physical deposit or mine" not in page
        assert b"nothing is uploaded automatically" in page
        assert b"external map link includes the displayed coordinates" in page
    assert b"27 researched cases</title>" in outputs[f"{core.DIRECTORY}/development.html"]
    assert b"33 blind cases</title>" in outputs[f"{core.DIRECTORY}/holdout.html"]


@pytest.mark.parametrize(
    "mutation",
    [
        "order",
        "missing",
        "wrong_id",
        "self_review",
        "no_external",
        "no_checks",
        "unsafe_url",
        "changed_proposal",
    ],
)
def test_incomplete_or_unbound_research_rejected(next_fixture, mutation):
    root, path, research, _ = next_fixture
    case = research["cases"][0]
    if mutation == "order":
        research["cases"].reverse()
    elif mutation == "missing":
        research["cases"].pop()
    elif mutation == "wrong_id":
        case["pair_id"] = "pair_1001"
    elif mutation == "self_review":
        case["second_review"]["reviewer"] = case["analyst"]
    elif mutation == "no_external":
        case["citations"][0]["access"] = "local_snapshot"
    elif mutation == "no_checks":
        case["second_review"]["verifications"] = []
    elif mutation == "unsafe_url":
        case["citations"][0]["url"] = "javascript:alert(1)"
    elif mutation == "changed_proposal":
        case["recommendation"] = "different_sites"
    path.write_bytes(json_bytes(research))
    with pytest.raises(ValueError):
        core.build(root, path)


def test_blind_data_rejects_nested_assessments(next_fixture):
    root, path, _, parents = next_fixture
    parents["holdout"]["cases"][0]["left"]["nested"] = {"recommendation": "same_site"}
    with pytest.raises(ValueError, match="Blind payload"):
        core.build(root, path)


def test_already_approved_case_cannot_reenter_batch(next_fixture):
    root, path, _, parents = next_fixture
    parents["holdout"]["cases"][0]["pair_id"] = "pair_1"
    with pytest.raises(ValueError, match="already approved"):
        core.build(root, path)


def test_reproducible_acceptance_and_output_conflicts(next_fixture):
    root, path, _, _ = next_fixture
    receipt = core.prepare(root, path)
    paths = [root / name for name in receipt["artifacts"]] + [root / core.RECEIPT]
    before = {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in paths}
    assert core.prepare(root) == receipt
    assert core.check(root, local=True) == receipt
    assert before == {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in paths}
    page = root / core.DIRECTORY / "holdout.html"
    page.write_text("Keep this existing file")
    with pytest.raises(ValueError):
        core.prepare(root)
    assert page.read_text() == "Keep this existing file"


def test_offline_check_needs_no_private_inputs(next_fixture):
    root, path, _, _ = next_fixture
    core.prepare(root, path)
    shutil.rmtree(root / "data/review")
    assert core.check(root)["holdout_recommendations"] == 0


def test_csvs_are_separately_bound_and_blank_is_not_complete(next_fixture):
    root, path, _, _ = next_fixture
    core.prepare(root, path)
    for part, count in core.PARTS.items():
        export = root / core.DIRECTORY / f"{part}-blank.csv"
        assert core.verify_labels(root, export, part)["counts"] == {"blank": count}
        with pytest.raises(ValueError, match="required answers"):
            core.verify_labels(root, export, part, complete=True)
    with pytest.raises(ValueError):
        core.verify_labels(root, root / core.DIRECTORY / "development-blank.csv", "holdout")


def test_completed_holdout_export_is_validated_without_import(next_fixture, tmp_path):
    root, path, _, _ = next_fixture
    core.prepare(root, path)
    sample = json.loads((root / core.DIRECTORY / "holdout.json").read_text())
    rows = list(csv.DictReader((root / core.DIRECTORY / "holdout-blank.csv").open()))
    for row in rows:
        row.update(label="insufficient_evidence", reviewer="Synthetic QA")
    export = tmp_path / "synthetic.csv"
    with export.open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(rows)
    result = core.verify_labels(root, export, "holdout", complete=True)
    assert result["all_cases_reviewed"]
    assert result["counts"] == {"insufficient_evidence": 33}
    assert json.loads((root / core.DIRECTORY / "holdout.json").read_text()) == sample
