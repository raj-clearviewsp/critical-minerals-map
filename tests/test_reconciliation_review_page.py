"""Evidence review stays offline and never leaks supplied decisions into held-out cases."""

import base64
import copy
import csv
import hashlib
import io
import json
import shutil
import subprocess
from html.parser import HTMLParser

import pytest

from pipeline.reconciliation.review_page import (
    _SCRIPT,
    render_review,
    sanitize_cases,
    sanitize_metadata,
)

HASH = "a" * 64
SECRET = "HIDDEN ASSESSMENT VALUE"


def test_unknown_source_date_is_preserved_as_missing():
    item = case()
    item["research"]["findings"][0]["source_date"] = None
    page = render_review([item], {"mode": "adjudication", "sample_sha256": HASH})
    assert b'"source_date": null' in page
    assert b"Your review review" not in page


def case(number=0):
    return {
        "pair_id": f"pair_{number}",
        "left_record_id": f"source_a:{number}",
        "right_record_id": f"source_b:{number}",
        "distance_km": 1.56,
        "shared_families": ["Ni"],
        "queue": SECRET,
        "features": {"value": SECRET},
        "reason_codes": [SECRET],
        "explanation": SECRET,
        "label": "same_site",
        "left": {
            "record_id": f"source_a:{number}",
            "source_id": "source_a",
            "display_name": "Example district",
            "entity_level": "district_area",
            "lat": 10,
            "lon": 20,
            "source_notes": ["Source wording retained"],
            "site_url": "https://publisher.example/record",
            "raw_status": {"status": "Active"},
        },
        "right": {
            "record_id": f"source_b:{number}",
            "source_id": "source_b",
            "display_name": "Example mine",
            "entity_level": "site",
            "lat": 10.01,
            "lon": 20.01,
        },
        "neighbors": [],
        "previous_decision": {
            "label": "same_site",
            "reviewer": "Prior reviewer",
            "notes": "Earlier answer remains intact",
        },
        "research": {
            "findings": [
                {
                    "claim": "Publisher distinguishes an area and a mine.",
                    "url": "https://publisher.example/evidence",
                    "title": "Publisher record",
                    "locator": "Table2, row4",
                    "source_date": "2025-01-01",
                }
            ],
            "recommendation": "Consider a containment relationship.",
            "unresolved_questions": ["Does the boundary include this mine?"],
        },
    }


class Page(HTMLParser):
    def __init__(self, content):
        super().__init__()
        self.tags, self.scripts, self.styles = [], [], []
        self.current, self.in_style = None, False
        self.feed(content)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        self.tags.append((tag, attrs))
        if tag == "script":
            self.scripts.append({"attrs": attrs, "text": ""})
            self.current = self.scripts[-1]
        if tag == "style":
            self.styles.append("")
            self.in_style = True

    def handle_data(self, data):
        if self.current is not None:
            self.current["text"] += data
        if self.in_style:
            self.styles[-1] += data

    def handle_endtag(self, tag):
        if tag == "script":
            self.current = None
        if tag == "style":
            self.in_style = False

    def payload(self):
        return json.loads(
            next(s["text"] for s in self.scripts if s["attrs"].get("id") == "review-data")
        )


def node_result(expression):
    executable = shutil.which("node")
    if executable is None:
        pytest.skip("Node unavailable")
    helpers = _SCRIPT.split("/* Browser application. */")[0]
    result = subprocess.run(
        [executable],
        input=helpers + "\nprocess.stdout.write(JSON.stringify(" + expression + "));",
        text=True,
        capture_output=True,
        check=True,
    )
    return json.loads(result.stdout)


@pytest.mark.parametrize("count", [1, 8, 20, 120, 200])
def test_variable_size_review_has_exact_cases_and_blank_new_decisions(count):
    cases = [case(i) for i in range(count)]
    metadata = {"sample_sha256": HASH, "mode": "development"}
    before = copy.deepcopy((cases, metadata))
    rendered = render_review(cases, metadata)
    assert rendered == render_review(cases, metadata)
    assert (cases, metadata) == before
    page = Page(rendered.decode())
    payload = page.payload()
    assert len(payload["cases"]) == count
    assert payload["cases"][0]["left"] == cases[0]["left"]
    radios = [attrs for tag, attrs in page.tags if tag == "input" and attrs.get("type") == "radio"]
    assert len(radios) == 6 and all("checked" not in attrs for attrs in radios)
    progress = next(attrs for tag, attrs in page.tags if tag == "progress")
    assert progress["max"] == str(count) and progress["value"] == "0"
    jump = next(attrs for tag, attrs in page.tags if attrs.get("id") == "case-jump")
    assert jump["max"] == str(count)
    state = node_result(f"restoreState({json.dumps(payload['cases'])}, null, '{HASH}')")
    assert all(value["label"] == "" for value in state["decisions"].values())


def test_holdout_payload_removes_assessments_answers_and_research_at_all_levels():
    item = case()
    item["left"]["features"] = {"value": SECRET}
    item["right"]["provenance"] = {"label": SECRET, "original_file": "publisher.xlsx"}
    metadata = {
        "sample_sha256": HASH,
        "mode": "holdout",
        "selection": SECRET,
        "previous_decision": SECRET,
        "queue": SECRET,
        "source_names": {"source_a": "Publisher"},
    }
    page = Page(render_review([item], metadata).decode())
    payload = page.payload()
    content = json.dumps(payload)
    assert SECRET not in content
    for forbidden in [
        "queue",
        "features",
        "reason_codes",
        "explanation",
        "previous_decision",
        "research",
        "label",
        "reviewer",
        "notes",
    ]:
        assert forbidden not in payload["cases"][0]
    assert payload["cases"][0]["right"]["provenance"] == {"original_file": "publisher.xlsx"}
    assert payload["cases"][0]["left"]["source_notes"] == ["Source wording retained"]
    assert "Prior reviewer" not in content and "containment relationship" not in content
    assert payload["cases"][0]["review_map"]["available"]
    assert "renderAssessment" not in _SCRIPT


def test_json_packet_and_renderer_share_the_same_sanitized_source_cases():
    cases = [case(), case(1)]
    metadata = {"sample_sha256": HASH, "mode": "holdout", "selection": SECRET}
    sanitized = sanitize_cases(cases, "holdout")
    clean_metadata = sanitize_metadata(metadata)
    assert "review_map" not in sanitized[0]
    assert "selection" not in clean_metadata
    assert sanitize_cases(sanitized, "holdout") == sanitized
    payload = Page(render_review(sanitized, clean_metadata).decode()).payload()
    for item in payload["cases"]:
        item.pop("review_map")
    assert payload == {"cases": sanitized, "metadata": clean_metadata}


@pytest.mark.parametrize("mode", ["development", "adjudication"])
def test_non_holdout_preserves_read_only_history_and_cited_research(mode):
    item = case()
    payload = Page(render_review([item], {"sample_sha256": HASH, "mode": mode}).decode()).payload()
    actual = payload["cases"][0]
    assert actual["previous_decision"] == item["previous_decision"]
    assert actual["research"] == item["research"]
    assert "label" not in actual and "queue" not in actual and "features" not in actual
    assert "This is an agent proposal, not a human label" in _SCRIPT


def test_updated_script_and_style_have_matching_csp_hashes_and_no_external_loads():
    page = Page(render_review([case()], {"sample_sha256": HASH, "mode": "adjudication"}).decode())
    csp = next(
        attrs["content"]
        for tag, attrs in page.tags
        if tag == "meta" and attrs.get("http-equiv") == "Content-Security-Policy"
    )
    script = next(s["text"] for s in page.scripts if not s["attrs"].get("type"))
    for text in [script, page.styles[0]]:
        checksum = base64.b64encode(hashlib.sha256(text.encode()).digest()).decode()
        assert f"'sha256-{checksum}'" in csp
    assert "connect-src 'none'" in csp and "default-src 'none'" in csp
    assert all("src" not in attrs and tag != "link" for tag, attrs in page.tags)
    assert "innerHTML" not in script
    assert "critical-minerals-atlas:validation:v1:" in script
    assert "atlas-validation-" in script and "atlas-triage-pilot-" not in script


def test_research_text_and_script_closers_cannot_create_html_or_execute_urls():
    item = case()
    evil = '</script><script>alert("unsafe")</script><img src=x onerror=alert(1)>'
    item["research"]["findings"][0].update(claim=evil, title=evil, url="javascript:alert(1)")
    item["research"]["recommendation"] = evil
    item["previous_decision"]["notes"] = evil
    rendered = render_review([item], {"sample_sha256": HASH, "mode": "adjudication"}).decode()
    page = Page(rendered)
    assert len(page.scripts) == 2
    assert not any(tag == "img" or "onerror" in attrs for tag, attrs in page.tags)
    assert page.payload()["cases"][0]["research"]["findings"][0]["claim"] == evil
    assert node_result("safeHttpsUrl('javascript:alert(1)')") is None
    assert node_result("safeHttpsUrl('https://name:secret@example.test/')") is None
    assert node_result("safeHttpsUrl('https://publisher.example/evidence')") == (
        "https://publisher.example/evidence"
    )


def test_csv_exports_exact_current_sample_and_new_decisions_only():
    cases = [case(i) for i in range(120)]
    decisions = {
        "pair_0": {"label": "left_contains_right", "notes": 'New "note"\nLine2'},
        "pair_1": {"label": "same_site", "notes": "=formula"},
    }
    result = node_result(
        f"buildCsv({json.dumps(cases)}, {json.dumps(decisions)}, 'Reviewer', '{HASH}')"
    )
    rows = list(csv.DictReader(io.StringIO(result.lstrip("\ufeff"))))
    assert len(rows) == 120
    assert rows[0]["label"] == "left_contains_right"
    assert rows[0]["notes"] == decisions["pair_0"]["notes"]
    assert rows[1]["notes"] == "'=formula"
    assert all(row["label"] == "" for row in rows[2:])
    assert all(row["sample_sha256"] == HASH for row in rows)
    assert "Earlier answer remains intact" not in result


def test_saved_progress_is_bound_to_sample_and_cannot_initialize_from_prior_answers():
    cases = [case()]
    saved = {
        "schema_version": 2,
        "sample_sha256": "b" * 64,
        "reviewer": "Reviewer",
        "decisions": {"pair_0": {"label": "same_site", "notes": "Wrong sample"}},
    }
    state = node_result(f"restoreState({json.dumps(cases)}, {json.dumps(saved)}, '{HASH}')")
    assert state["decisions"]["pair_0"] == {"label": "", "notes": ""}
    assert state["reviewer"] == ""


@pytest.mark.parametrize(
    "change",
    [
        "empty",
        "too_many",
        "duplicate_id",
        "duplicate_pair",
        "self",
        "wrong_side",
        "nan",
        "negative",
        "bool_distance",
    ],
)
def test_invalid_case_sets_are_rejected(change):
    cases = [case(), case(1)]
    if change == "empty":
        cases = []
    elif change == "too_many":
        cases = [case(i) for i in range(201)]
    elif change == "duplicate_id":
        cases[1]["pair_id"] = cases[0]["pair_id"]
    elif change == "duplicate_pair":
        cases[1] = {**copy.deepcopy(cases[0]), "pair_id": "different_id"}
    elif change == "self":
        cases[0]["right_record_id"] = cases[0]["left_record_id"]
    elif change == "wrong_side":
        cases[0]["right"]["record_id"] = "wrong"
    elif change == "nan":
        cases[0]["distance_km"] = float("nan")
    elif change == "negative":
        cases[0]["distance_km"] = -1
    else:
        cases[0]["distance_km"] = True
    with pytest.raises(ValueError):
        render_review(cases, {"sample_sha256": HASH, "mode": "development"})


@pytest.mark.parametrize(
    "metadata",
    [
        {"sample_sha256": HASH},
        {"sample_sha256": HASH, "mode": "wrong"},
        {"sample_sha256": "bad", "mode": "holdout"},
        {"sample_sha256": HASH, "mode": "holdout", "title": 3},
    ],
)
def test_invalid_metadata_is_rejected(metadata):
    with pytest.raises(ValueError):
        render_review([case()], metadata)
