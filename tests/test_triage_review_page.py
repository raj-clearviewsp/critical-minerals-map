"""The focused pilot preserves prior answers and maps only supplied source coordinates."""

import base64
import copy
import csv
import hashlib
import io
import json
import math
import shutil
import subprocess
from html.parser import HTMLParser

import pytest

from pipeline.triage.review_page import _SCRIPT, _project, map_data, render_pilot

HASH = "a" * 64


def case(number=0, *, a=(10, 20), b=(10.01, 20.01)):
    return {
        "pair_id": f"pair_{number}",
        "left_record_id": f"source_a:{number}",
        "right_record_id": f"source_b:{number}",
        "distance_km": 1.56,
        "shared_families": ["Ni"],
        "queue": "relationship",
        "reason_codes": ["broad_scope"],
        "explanation": "A describes a district; B describes a mine.",
        "features": {"left_entity_level": "district_area", "right_entity_level": "site"},
        "left": {
            "record_id": f"source_a:{number}",
            "source_id": "source_a",
            "display_name": "Example district",
            "primary_name": "Example district",
            "entity_level": "district_area",
            "lat": a[0],
            "lon": a[1],
            "site_url": "https://publisher.example/record",
            "context_names": ["Mine within district"],
            "deposit_types": ["magmatic_sulfide"],
        },
        "right": {
            "record_id": f"source_b:{number}",
            "source_id": "source_b",
            "display_name": "Example mine",
            "entity_level": "site",
            "lat": b[0],
            "lon": b[1],
        },
        "neighbors": [],
        "previous_decision": {
            "label": "same_site",
            "reviewer": "Reviewer",
            "notes": "Preserve this note",
            "original_sample_sha256": "b" * 64,
        },
    }


def cases():
    return [case(i) for i in range(20)]


class Page(HTMLParser):
    def __init__(self, content):
        super().__init__()
        self.tags, self.scripts, self.styles = [], [], []
        self.current = None
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


def test_render_is_deterministic_and_does_not_mutate_original_decisions():
    items = cases()
    metadata = {"sample_sha256": HASH}
    before = copy.deepcopy((items, metadata))
    result = render_pilot(items, metadata)
    assert result == render_pilot(items, metadata)
    assert (items, metadata) == before
    page = Page(result.decode())
    radio = [attrs for tag, attrs in page.tags if tag == "input" and attrs.get("type") == "radio"]
    assert len(radio) == 6 and all("checked" not in attrs for attrs in radio)
    assert {r["value"] for r in radio} == {
        "same_site",
        "different_sites",
        "left_contains_right",
        "right_contains_left",
        "related_scope_unclear",
        "insufficient_evidence",
    }
    payload = json.loads(
        next(s["text"] for s in page.scripts if s["attrs"].get("id") == "review-data")
    )
    assert len(payload["cases"]) == 20
    assert payload["cases"][0]["previous_decision"] == items[0]["previous_decision"]
    assert all(c["review_map"]["available"] for c in payload["cases"])
    assert "not the final accuracy benchmark" in result.decode()


def test_prior_labels_and_machine_queue_never_initialize_pilot_decisions():
    items = cases()
    for item in items:
        item["label"] = "same_site"
    state = node_result(f"restoreState({json.dumps(items)}, null, '{HASH}')")
    assert state["reviewer"] == ""
    assert all(v == {"label": "", "notes": ""} for v in state["decisions"].values())
    saved = {
        "schema_version": 2,
        "sample_sha256": HASH,
        "reviewer": "New reviewer",
        "decisions": {
            "pair_0": {"label": "left_contains_right", "notes": "New evidence"},
            "pair_1": {"label": "likely_match", "notes": "Machine value"},
        },
    }
    state = node_result(f"restoreState({json.dumps(items)}, {json.dumps(saved)}, '{HASH}')")
    assert state["decisions"]["pair_0"]["label"] == "left_contains_right"
    assert state["decisions"]["pair_1"]["label"] == ""
    saved["sample_sha256"] = "c" * 64
    state = node_result(f"restoreState({json.dumps(items)}, {json.dumps(saved)}, '{HASH}')")
    assert all(not v["label"] for v in state["decisions"].values())


def test_csv_exports_twenty_exact_ids_and_relationships_without_old_decisions():
    items = cases()
    decisions = {
        "pair_0": {"label": "left_contains_right", "notes": 'A "contains" B\nSource says so'},
        "pair_1": {"label": "insufficient_evidence", "notes": "=unsafe formula"},
    }
    output = node_result(
        f"buildCsv({json.dumps(items)}, {json.dumps(decisions)}, 'Reviewer', '{HASH}')"
    )
    rows = list(csv.DictReader(io.StringIO(output.lstrip("\ufeff"))))
    assert len(rows) == 20
    assert list(rows[0]) == [
        "pair_id",
        "left_record_id",
        "right_record_id",
        "label",
        "reviewer",
        "notes",
        "sample_sha256",
    ]
    assert (
        rows[0]["label"] == "left_contains_right"
        and rows[0]["notes"] == decisions["pair_0"]["notes"]
    )
    assert rows[1]["notes"] == "'=unsafe formula"
    assert all(row["label"] == "" for row in rows[2:])
    assert all(row["sample_sha256"] == HASH for row in rows)
    assert [r["left_record_id"] for r in rows] == [c["left_record_id"] for c in items]


def test_map_projects_known_equatorial_kilometres_and_keeps_metric_axis_scale():
    east, north = _project(0, 1, 0, 0)
    assert east == pytest.approx(111.19508, abs=1e-5)
    assert north == pytest.approx(0)
    east, north = _project(1, 0, 0, 0)
    assert east == pytest.approx(0) and north == pytest.approx(111.19508, abs=1e-5)
    mapped = map_data(case(a=(0, 0), b=(0.01, 0.02)))
    assert mapped["available"]
    points = mapped["points"]
    delta = math.dist((points[0]["x"], points[0]["y"]), (points[1]["x"], points[1]["y"]))
    distance = math.dist(
        (points[0]["east_km"], points[0]["north_km"]), (points[1]["east_km"], points[1]["north_km"])
    )
    assert delta / distance == pytest.approx(mapped["scale_px"] / mapped["scale_km"])


@pytest.mark.parametrize(
    "a,b",
    [
        ((0, 179.99), (0, -179.99)),
        ((89.999, 0), (89.999, 180)),
        ((-89.999, -90), (-89.999, 90)),
        ((90, 0), (89.99, 180)),
    ],
)
def test_local_map_has_no_dateline_or_polar_seam(a, b):
    result = map_data(case(a=a, b=b))
    assert result["available"]
    assert result["extent_east_km"] < 10 and result["extent_north_km"] < 10
    assert all(math.isfinite(p[k]) for p in result["points"] for k in ("x", "y"))
    assert all(45 <= p["x"] <= 755 and 35 <= p["y"] <= 305 for p in result["points"])


def test_coordinate_reuse_is_a_single_marker_without_jitter_or_identity_claim():
    item = case(a=(1, 2), b=(1, 2))
    item["neighbors"] = [dict(item["left"], record_id="third", display_name="Different named site")]
    before = copy.deepcopy(item)
    result = map_data(item)
    assert len(result["markers"]) == 1
    marker = result["markers"][0]
    assert marker["label"] == "A/B" and marker["count"] == 3
    assert all(p["lat"] == 1 and p["lon"] == 2 for p in result["points"])
    assert len({(p["x"], p["y"]) for p in result["points"]}) == 1
    assert item == before


def test_invalid_and_distant_neighbors_do_not_expand_local_map():
    item = case(a=(0, 0), b=(0, 0.01))
    item["neighbors"] = [
        {"record_id": "far", "lat": 0, "lon": 180},
        {"record_id": "invalid", "lat": 1000, "lon": 0},
        {"record_id": item["left_record_id"], "lat": 0, "lon": 0},
    ]
    result = map_data(item)
    assert result["available"] and result["omitted_neighbors"] == 2
    assert len(result["points"]) == 2
    item["left"]["lat"] = None
    assert not map_data(item)["available"]
    assert not map_data(case(a=(0, 0), b=(0, 180)))["available"]


def test_source_text_cannot_escape_json_template_or_create_external_requests():
    items = cases()
    attack = '</script><script src="https://bad.example">attack()</script>__SCRIPT__'
    items[0]["left"]["display_name"] = attack
    items[0]["explanation"] = '<img src=x onerror="attack()">'
    content = render_pilot(items, {"sample_sha256": HASH, "title": attack}).decode()
    page = Page(content)
    assert len(page.scripts) == 2
    data = json.loads(
        next(s["text"] for s in page.scripts if s["attrs"].get("id") == "review-data")
    )
    assert data["cases"][0]["left"]["display_name"] == attack
    assert attack not in content
    assert "innerHTML" not in _SCRIPT and "fetch(" not in _SCRIPT
    csp = next(
        a["content"]
        for t, a in page.tags
        if t == "meta" and a.get("http-equiv") == "Content-Security-Policy"
    )
    assert "connect-src 'none'" in csp and "default-src 'none'" in csp
    for code in [
        page.styles[0],
        next(s["text"] for s in page.scripts if not s["attrs"].get("type")),
    ]:
        checksum = base64.b64encode(hashlib.sha256(code.encode()).digest()).decode()
        assert f"'sha256-{checksum}'" in csp


def test_external_links_are_https_without_credentials():
    values = [
        "https://source.example/x",
        "http://source.example",
        "javascript:attack()",
        "https://user:secret@source.example",
        "https://source.example\n/x",
        "//source.example",
    ]
    result = node_result(f"{json.dumps(values)}.map(safeHttpsUrl)")
    assert result == [values[0], None, None, None, None, None]


@pytest.mark.parametrize("value", [None, "x", "A" * 64])
def test_invalid_metadata_digest_is_rejected(value):
    with pytest.raises(ValueError, match="SHA-256"):
        render_pilot(cases(), {"sample_sha256": value})


def test_wrong_count_duplicate_ids_changed_side_and_nonfinite_distance_are_rejected():
    with pytest.raises(ValueError, match="20 cases"):
        render_pilot([case()], {"sample_sha256": HASH})
    assert render_pilot([case()], {"sample_sha256": HASH, "allow_test_sample": True})
    items = cases()
    items[-1] = items[0]
    with pytest.raises(ValueError, match="unique"):
        render_pilot(items, {"sample_sha256": HASH})
    items = cases()
    items[0]["left"]["record_id"] = "wrong"
    with pytest.raises(ValueError, match="must match"):
        render_pilot(items, {"sample_sha256": HASH})
    items = cases()
    items[0]["distance_km"] = float("nan")
    with pytest.raises(ValueError, match="finite"):
        render_pilot(items, {"sample_sha256": HASH})


def test_full_script_syntax_and_compact_evidence():
    executable = shutil.which("node")
    if executable is None:
        pytest.skip("Node unavailable")
    subprocess.run(
        [executable, "--check"], input=_SCRIPT, text=True, capture_output=True, check=True
    )
    record = {"pilot_evidence": [{"raw_value": "Nickel", "role": "primary"}] * 30}
    assert node_result(f"compactTerms({json.dumps(record)})") == {
        "items": ["Nickel (primary)"],
        "more": 0,
    }
    assert node_result("scopeName('district_area')") == "District or area"
    assert node_result("scopeName('unknown')") == "Scope needs clarification"


def test_context_grid_groups_triage_and_prior_beside_map_without_smaller_body_type():
    content = render_pilot(cases(), {"sample_sha256": HASH}).decode()
    context = content.split('<div class="context-grid">', 1)[1].split('<section id="records"', 1)[0]
    assert 'id="assessment"' in context and 'id="prior"' in context and 'id="map"' in context
    assert content.count('id="prior"') == 1
    assert "font-size: 18px" in content
    assert "grid-template-columns: minmax(0, 2fr) minmax(0, 3fr)" in content
    assert "@media (max-width: 1099px)" in content
    assert 'externalLink(record.site_url, "Open source evidence")' in _SCRIPT
