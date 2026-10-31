"""Local review must not turn source text or a sampling hint into a human label."""

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

from pipeline.link.review_page import _SCRIPT, render_review

HASH = "a" * 64


def pair(pair_id="p1"):
    return {
        "pair_id": pair_id,
        "stratum": "internal sampling category",
        "distance_km": 0.5,
        "shared_families": ["Ni"],
        "left": {
            "record_id": "source-a:17",
            "source_id": "source-a",
            "name": "First name",
            "source_url": "https://publisher.example/record",
            "aliases": ["Old name"],
        },
        "right": {"record_id": "source-b:21", "source_id": "source-b", "name": "Second name"},
    }


class Page(HTMLParser):
    def __init__(self, content):
        super().__init__()
        self.tags = []
        self.scripts = []
        self.current = None
        self.styles = []
        self.in_style = False
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


def node_result(expression):
    executable = shutil.which("node")
    if executable is None:
        pytest.skip("Node is unavailable; browser-independent JavaScript checks require Node")
    helpers = _SCRIPT.split("/* Browser application. */")[0]
    script = helpers + "\nprocess.stdout.write(JSON.stringify(" + expression + "));"
    result = subprocess.run([executable], input=script, text=True, capture_output=True, check=True)
    return json.loads(result.stdout)


def test_html_is_deterministic_and_never_mutates_source_pairs():
    pairs = [pair()]
    metadata = {"sample_sha256": HASH, "title": "Review these pairs"}
    original = copy.deepcopy((pairs, metadata))
    content = render_review(pairs, metadata)
    assert content == render_review(pairs, metadata)
    assert (pairs, metadata) == original
    page = Page(content.decode())
    radios = [attrs for tag, attrs in page.tags if tag == "input" and attrs.get("type") == "radio"]
    assert {r["value"] for r in radios} == {"same_site", "different_sites", "uncertain"}
    assert all("checked" not in attrs for attrs in radios)
    assert b"Uncertain pairs require adjudication" in content
    assert b"@media print" in content and b"@media (max-width: 780px)" in content


def test_untrusted_source_text_cannot_break_json_script_or_template():
    sample = pair()
    attack = '</script><script src="https://bad.example/x.js">alert(1)</script>__SCRIPT__'
    sample["left"]["name"] = attack
    sample["right"]["raw_status"] = {"status": '<img src=x onerror="alert(1)">'}
    content = render_review([sample], {"sample_sha256": HASH, "title": attack}).decode()
    page = Page(content)
    assert len(page.scripts) == 2
    payload = next(s for s in page.scripts if s["attrs"].get("id") == "review-data")
    assert json.loads(payload["text"])["pairs"][0]["left"]["name"] == attack
    assert attack not in content
    assert all("src" not in script["attrs"] for script in page.scripts)
    assert "innerHTML" not in _SCRIPT and "textContent" in _SCRIPT


def test_csp_binds_local_code_and_disallows_remote_runtime_requests():
    page = Page(render_review([pair()], {"sample_sha256": HASH}).decode())
    csp = next(
        attrs["content"]
        for tag, attrs in page.tags
        if tag == "meta" and attrs.get("http-equiv") == "Content-Security-Policy"
    )
    assert "connect-src 'none'" in csp and "default-src 'none'" in csp
    executable = next(s["text"] for s in page.scripts if not s["attrs"].get("type"))
    for code in [executable, page.styles[0]]:
        digest = base64.b64encode(hashlib.sha256(code.encode()).digest()).decode()
        assert f"'sha256-{digest}'" in csp
    assert "fetch(" not in executable and "XMLHttpRequest" not in executable
    assert not any(
        tag == "link" and attrs.get("href", "").startswith("http") for tag, attrs in page.tags
    )


@pytest.mark.parametrize("value", ["", "abc", "A" * 64, None])
def test_invalid_sample_hash_fails(value):
    with pytest.raises(ValueError, match="SHA-256"):
        render_review([pair()], {"sample_sha256": value})


def test_pair_identity_is_required_and_unique():
    with pytest.raises(ValueError, match="unique"):
        render_review([pair(), pair()], {"sample_sha256": HASH})
    bad = pair()
    bad["right"]["record_id"] = bad["left"]["record_id"]
    with pytest.raises(ValueError, match="distinct"):
        render_review([bad], {"sample_sha256": HASH})
    with pytest.raises(ValueError, match="at least one"):
        render_review([], {"sample_sha256": HASH})


def test_saved_progress_only_restores_known_labels_for_the_same_sample():
    pairs = [pair(), pair("p2")]
    saved = {
        "schema_version": 1,
        "sample_sha256": HASH,
        "reviewer": "Reviewer",
        "decisions": {
            "p1": {"label": "uncertain", "notes": "Needs source check"},
            "p2": {"label": "suggested_same", "notes": "No automatic label"},
            "unknown_pair": {"label": "same_site"},
        },
    }
    restored = node_result(
        f"restoreState({json.dumps(pairs)}, {json.dumps(saved)}, {json.dumps(HASH)})"
    )
    assert restored["decisions"]["p1"] == {"label": "uncertain", "notes": "Needs source check"}
    assert restored["decisions"]["p2"]["label"] == ""
    assert set(restored["decisions"]) == {"p1", "p2"}
    wrong = node_result(
        f"restoreState({json.dumps(pairs)}, {json.dumps(saved)}, {'"b' + 'b' * 63 + '"'})"
    )
    assert wrong["reviewer"] == ""
    assert all(not decision["label"] for decision in wrong["decisions"].values())
    pairs[0]["label"] = "same_site"
    fresh = node_result(f"restoreState({json.dumps(pairs)}, null, {json.dumps(HASH)})")
    assert all(not decision["label"] for decision in fresh["decisions"].values())


def test_csv_export_preserves_ids_partial_labels_quotes_and_notes():
    pairs = [pair(), pair("p2")]
    decisions = {
        "p1": {"label": "uncertain", "notes": 'Name "A", old source\nCheck location'},
        "p2": {"label": "", "notes": ""},
    }
    output = node_result(
        f"buildCsv({json.dumps(pairs)}, {json.dumps(decisions)}, 'Reviewer', '{HASH}')"
    )
    rows = list(csv.DictReader(io.StringIO(output.lstrip("\ufeff"))))
    assert len(rows) == 2
    assert rows[0] == {
        "pair_id": "p1",
        "left_record_id": "source-a:17",
        "right_record_id": "source-b:21",
        "label": "uncertain",
        "reviewer": "Reviewer",
        "notes": decisions["p1"]["notes"],
        "sample_sha256": HASH,
    }
    assert rows[1]["label"] == ""
    formula = node_result("csvCell('=HYPERLINK(\"https://bad.example\")')")
    assert next(csv.reader([formula]))[0].startswith("'=")


def test_publisher_links_accept_only_credential_free_https():
    values = [
        "https://publisher.example/a?b=1",
        "http://publisher.example",
        "javascript:alert(1)",
        "https://user:secret@publisher.example",
        "https://publisher.example\n/evil",
        "//publisher.example",
        "data:text/html,x",
        "https:publisher.example",
    ]
    result = node_result(f"{json.dumps(values)}.map(safeHttpsUrl)")
    assert result[0] == values[0]
    assert result[1:] == [None] * 7


def test_full_browser_script_is_valid_javascript():
    executable = shutil.which("node")
    if executable is None:
        pytest.skip("Node is unavailable")
    subprocess.run(
        [executable, "--check"], input=_SCRIPT, text=True, capture_output=True, check=True
    )


def test_compact_commodity_facts_deduplicate_linked_rows_and_bound_overflow():
    entries = [
        {
            "field": "commod",
            "raw_value": "Nickel",
            "role": "primary",
            "codes": ["Ni"],
            "qualified": False,
            "source_path": f"/child/{i}",
        }
        for i in range(100)
    ]
    record = {
        "pilot_evidence": entries,
        "raw_commodities": {"Commodity.txt": [{"metadata": "not a main fact"}] * 100},
    }
    result = node_result(f"compactCommodities({json.dumps(record)})")
    assert len(result["items"]) == 1 and result["remaining"] == 0
    assert result["items"][0]["term"] == "Nickel"
    assert result["items"][0]["role"] == "primary"
    record["pilot_evidence"] += [dict(entries[0], raw_value=f"Reported term {i}") for i in range(8)]
    result = node_result(f"compactCommodities({json.dumps(record)})")
    assert len(result["items"]) == 6 and result["remaining"] == 3


def test_missing_iso_shows_raw_country_without_claiming_an_iso_assignment():
    record = {"country_iso3s": [], "country": "CANADA:FIPS"}
    result = node_result(f"countrySummary({json.dumps(record)})")
    assert result == "CANADA:FIPS (as reported; ISO country unresolved)"
    record["country_iso3s"] = ["CAN"]
    assert node_result(f"countrySummary({json.dumps(record)})") == "CAN"


def test_unknown_precision_policy_is_not_exposed_as_main_label():
    record = {
        "location_precision": None,
        "precision_policy": "unknown precision; review only; ineligible for future auto-merge",
    }
    assert node_result(f"precisionSummary({json.dumps(record)})") == "Not stated by source"
    record["location_precision"] = "approximate"
    assert node_result(f"precisionSummary({json.dumps(record)})") == "Approximate (as reported)"


def test_long_pair_id_and_full_commodity_fields_are_only_in_details():
    content = render_review([pair("b" * 64)], {"sample_sha256": HASH}).decode()
    heading = content.split('<div class="pair-heading">', 1)[1].split("</div>", 1)[0]
    assert 'id="pair-id"' not in heading
    footer = content.split("<footer>", 1)[1].split("</footer>", 1)[0]
    assert 'id="pair-id"' in footer
    assert '["All original commodity fields", "raw_commodities"]' in _SCRIPT
    assert 'fact(facts, "Commodities as reported", record.raw_commodities)' not in _SCRIPT
