"""Triage preserves original decisions and binds new labels to the exact pilot."""

import copy
import csv
import io
import json
from collections import Counter

import pytest

from pipeline.link.candidates import haversine_km
from pipeline.link.labels import HEADERS
from pipeline.normalize.core import NormalizeError, sha256
from pipeline.scaffold import json_bytes
from pipeline.triage import core, labels
from pipeline.triage.rules import triage_pairs

PRIVATE_NAME = "PRIVATE SOURCE NAME"
PRIVATE_REVIEWER = "PRIVATE REVIEWER"
PRIVATE_NOTE = 'PRIVATE NOTE: "quoted", café\nSecond line with trailing space '


def record(number, side, name, *, level="site", separation=0):
    source = "gseu_crm" if side == "a" else "usgs_us_critical"
    return {
        "record_id": f"{source}:{number}",
        "source_id": source,
        "native_id": str(number),
        "primary_name": name,
        "display_name": PRIVATE_NAME,
        "raw_name": name,
        "primary_name_usable": True,
        "parent_name": None,
        "identity_aliases": [],
        "unverified_aliases": [],
        "context_names": [],
        "aliases_raw": [],
        "alias_evidence": [],
        "entity_level": level,
        "entity_level_evidence": [],
        "scope_flags": [],
        "deposit_types": [],
        "deposit_type_evidence": [],
        "deposit_types_raw": {},
        "site_url": None,
        "source_url": "https://example.test/source",
        "source_links": [],
        "source_notes": [],
        "reference": {},
        "provenance": {},
        "location_precision": None,
        "lat": -70 + number * 0.5 + separation,
        "lon": 20,
        "country": "Country as reported",
        "country_iso3s": [],
        "pilot_families": ["Ni"],
        "pilot_commodities": ["Ni"],
        "raw_commodities": {},
        "raw_status": {},
        "pilot_evidence": [],
        "record_kind": "deposit",
        "source_view": None,
    }


def candidate(left, right, number):
    return {
        "pair_id": f"pair_{number:03}",
        "left_record_id": left["record_id"],
        "right_record_id": right["record_id"],
        "left_source_id": left["source_id"],
        "right_source_id": right["source_id"],
        "source_pair": sorted([left["source_id"], right["source_id"]]),
        "shared_families": ["Ni"],
        "radius_km": 10,
        "distance_km": haversine_km(left["lat"], left["lon"], right["lat"], right["lon"]),
        "name_category": "original category preserved",
    }


def csv_bytes(rows, *, bom=False):
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=HEADERS, lineterminator="\r\n")
    writer.writeheader()
    writer.writerows(rows)
    return (b"\xef\xbb\xbf" if bom else b"") + stream.getvalue().encode()


@pytest.fixture
def checkpoint(tmp_path, monkeypatch):
    records, candidates = {}, []
    for number in range(200):
        letters = chr(65 + number // 26) + chr(65 + number % 26)
        if number < 10 or 100 <= number < 125:
            left = record(number, "a", "District" + letters, level="district_area")
            right = record(number, "b", "Orchid" + letters, separation=0.02)
        elif 20 <= number < 60:
            left = record(number, "a", "Orchid" + letters)
            right = record(number, "b", "Orchid" + letters, separation=0.001)
        elif 60 <= number < 100:
            left = record(number, "a", "Borealis" + letters)
            right = record(number, "b", "Ziranda" + letters, separation=0.045)
        else:
            left = record(number, "a", "Borealis" + letters)
            right = record(number, "b", "Ziranda" + letters, separation=0.005)
        records.update({left["record_id"]: left, right["record_id"]: right})
        candidates.append(candidate(left, right, number))
    original = {
        "eligible_rows": len(records),
        "candidate_pairs": len(candidates),
        "sample_sha256": "a" * 64,
    }
    sample = {
        "sample_sha256": original["sample_sha256"],
        "pairs": candidates,
        "source_names": {"gseu_crm": "Geological publisher", "usgs_us_critical": "USGS"},
    }
    (tmp_path / "data/review/linkage").mkdir(parents=True)
    (tmp_path / "reports").mkdir()
    (tmp_path / "pipeline/triage").mkdir(parents=True)
    (tmp_path / "pipeline/triage/__init__.py").write_text("# synthetic implementation\n")
    (tmp_path / "uv.lock").write_text("synthetic locked dependencies\n")
    (tmp_path / "reports/linkage-review-audit.json").write_bytes(json_bytes(original))
    (tmp_path / "data/review/linkage/sample.json").write_bytes(json_bytes(sample))
    (tmp_path / "data/review/linkage/candidates.json").write_bytes(json_bytes(candidates))
    export = []
    for number, pair in enumerate(candidates):
        label = (
            "same_site"
            if number < 11
            else "different_sites"
            if number < 16
            else ("uncertain" if number < 20 else "")
        )
        export.append(
            {
                **{key: pair[key] for key in HEADERS[:3]},
                "label": label,
                "reviewer": PRIVATE_REVIEWER,
                "notes": PRIVATE_NOTE + str(number) if label else "",
                "sample_sha256": original["sample_sha256"],
            }
        )
    csv_path = tmp_path / "user-export.csv"
    csv_path.write_bytes(csv_bytes(export, bom=True))
    calls = []

    def prior_check(root, *, raw=False):
        assert root == tmp_path
        calls.append(raw)
        return copy.deepcopy(original)

    monkeypatch.setattr(core, "check_original", prior_check)
    monkeypatch.setattr(core, "load_records", lambda root: copy.deepcopy(records))
    receipt = core.prepare(tmp_path, csv_path)
    return tmp_path, original, sample, csv_path, export, records, candidates, receipt, calls


def test_original_twenty_decisions_and_csv_bytes_are_preserved_exactly(checkpoint):
    root, _, _, csv_path, export, _, _, receipt, calls = checkpoint
    assert calls == [True]
    assert (root / core.DIRECTORY / "original-decisions.csv").read_bytes() == csv_path.read_bytes()
    preserved = json.loads((root / core.DIRECTORY / "preserved-decisions.json").read_text())
    assert preserved["reviewed_rows"] == 20
    assert preserved["decisions"] == export[:20]
    assert preserved["counts"] == {
        "same_site": 11,
        "different_sites": 5,
        "uncertain": 4,
        "blank": 180,
    }
    assert preserved["original_csv_sha256"] == sha256(csv_path)
    assert receipt["import"]["reviewed_rows"] == 20
    checks = json.loads((root / core.DIRECTORY / "prior-decision-checks.json").read_text())
    assert [item["original_decision"] for item in checks] == export[:20]


def test_pilot_has_six_clarifications_fourteen_new_cases_and_blank_new_labels(checkpoint):
    root, _, _, _, export, _, _, receipt, _ = checkpoint
    pilot = labels.load_bound_sample(root, receipt)
    assert len(pilot["cases"]) == len({case["pair_id"] for case in pilot["cases"]}) == 20
    assert Counter(case["queue"] for case in pilot["cases"]) == core.QUOTAS
    assert pilot["selection"]["previous_decisions_shown"] == 6
    assert pilot["selection"]["new_cases"] == 14
    assert sum(case["previous_decision"] is not None for case in pilot["cases"]) == 6
    previous = {row["pair_id"]: row for row in export[:20]}
    for case in pilot["cases"]:
        assert case["label"] == case["reviewer"] == case["notes"] == ""
        if case["previous_decision"]:
            assert case["previous_decision"] == previous[case["pair_id"]]
    assert receipt["auto_merges"] == receipt["human_labels_created"] == 0
    assert not (root / "data/canonical").exists()


def test_source_view_duplicates_never_consume_pilot_slots(checkpoint):
    _, _, _, _, export, records, candidates, _, _ = checkpoint
    rows = triage_pairs(candidates, records, {"pairs": []})
    duplicate = next(row for row in rows if row["queue"] == "likely_match")
    duplicate["disposition"] = "source_view_duplicate"
    previous = {row["pair_id"]: row for row in export[:20]}
    chosen, selection = core.select_cases(rows, previous)
    assert duplicate["pair_id"] not in {case["pair_id"] for case in chosen}
    assert len(chosen) == 20 and selection["previous_decisions_shown"] == 6
    assert (chosen, selection) == core.select_cases(list(reversed(rows)), previous)


def test_unit_routing_conflict_requires_adjudication_without_changing_member(checkpoint):
    _, _, _, _, _, records, candidates, _, _ = checkpoint
    row = next(
        row
        for row in triage_pairs(candidates, records, {"pairs": []})
        if row["queue"] == "likely_match"
    )
    original = copy.deepcopy(row)
    unit = {
        "unit_id": "synthetic_unit",
        "member_pair_ids": [row["pair_id"]],
        "representative_pair_id": row["pair_id"],
        "reason_codes": ["publisher_view_routing_disagreement"],
        "queue": "ambiguous",
        "queue_conflict": True,
        "requires_adjudication": True,
    }
    result = core.unit_representatives([row], {"units": [unit]}, {})[0]
    assert result["queue"] == "ambiguous"
    assert result["requires_adjudication"] is True
    assert result["disposition"] == "source_view_routing_conflict"
    assert "source_view_routing_conflict" in result["reason_codes"]
    assert row == original
    unit.update(queue="likely_match", queue_conflict=False, requires_adjudication=False)
    result = core.unit_representatives([row], {"units": [unit]}, {})[0]
    assert result["disposition"] == row["disposition"]
    assert result["requires_adjudication"] is False


def test_repeat_acceptance_and_checks_preserve_all_bytes_and_mtimes(checkpoint):
    root, _, _, csv_path, _, _, _, receipt, _ = checkpoint
    paths = [path for path in root.rglob("*") if path.is_file()]
    before = {path: (sha256(path), path.stat().st_mtime_ns) for path in paths}
    assert core.prepare(root, csv_path) == receipt
    assert core.check(root) == receipt
    assert core.check(root, raw=True) == receipt
    assert {path: (sha256(path), path.stat().st_mtime_ns) for path in paths} == before


def test_public_receipt_and_counts_have_no_names_reviewers_or_notes(checkpoint):
    root, _, _, _, _, _, _, receipt, _ = checkpoint
    content = json_bytes(receipt) + (root / core.COUNTS).read_bytes()
    for private in (PRIVATE_NAME, PRIVATE_REVIEWER, "PRIVATE NOTE"):
        assert private.encode() not in content


def test_changed_packet_is_rejected_without_overwriting(checkpoint):
    root, _, _, csv_path, _, _, _, _, _ = checkpoint
    path = root / core.DIRECTORY / "index.html"
    path.write_bytes(b"modified private pilot")
    with pytest.raises(NormalizeError, match="Existing staging artifact differs"):
        core.prepare(root, csv_path)
    assert path.read_bytes() == b"modified private pilot"
    with pytest.raises(core.TriageError, match="Private triage artifact changed"):
        core.check(root, raw=True)


@pytest.mark.parametrize(
    "field,value",
    [
        ("source_view_pairs", 1),
        ("source_view_groups", 1),
        ("informative_deposit_type_records", -1),
        ("informative_deposit_type_records", 401),
    ],
)
def test_impossible_public_counts_are_rejected(checkpoint, field, value):
    _, original, _, _, _, _, _, receipt, _ = checkpoint
    changed = copy.deepcopy(receipt)
    changed[field] = value
    with pytest.raises(core.TriageError):
        core.validate_receipt(changed, original)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda r: r["import"]["counts"].update(same_site=31, different_sites=-15),
        lambda r: r["import"]["counts"].__setitem__("invented_label", 0),
        lambda r: r["import"]["counts"].__setitem__("same_site", 11.0),
        lambda r: r["import"].__setitem__("reviewed_rows", 20.0),
        lambda r: r["import"].__setitem__("csv_rows", 200.0),
        lambda r: r["selection"].__setitem__("previous_decisions_shown", 6.0),
        lambda r: r["selection"].__setitem__("new_cases", 14.0),
    ],
)
def test_import_and_selection_histograms_require_real_nonnegative_integer_counts(
    checkpoint, mutate
):
    _, original, _, _, _, _, _, receipt, _ = checkpoint
    changed = copy.deepcopy(receipt)
    mutate(changed)
    with pytest.raises(core.TriageError, match="Preserved-decision or pilot counts"):
        core.validate_receipt(changed, original)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda r: r["queue_counts"].__setitem__("ambiguous", 999),
        lambda r: r["selection"].__setitem__("new_cases", 19),
        lambda r: r["import"].__setitem__("reviewed_rows", 19),
        lambda r: r.__setitem__("sample_sha256", "G" * 64),
        lambda r: r["artifacts"].__setitem__(
            "outside/private.json", {"sha256": "a" * 64, "bytes": 1}
        ),
    ],
)
def test_receipt_tampering_fails(checkpoint, mutate):
    _, original, _, _, _, _, _, receipt, _ = checkpoint
    changed = copy.deepcopy(receipt)
    mutate(changed)
    with pytest.raises(core.TriageError):
        core.validate_receipt(changed, original)


def pilot_export(root, receipt):
    sample = labels.load_bound_sample(root, receipt)
    content = labels.blank_csv(sample["cases"], sample["sample_sha256"])
    return sample, list(csv.DictReader(io.StringIO(content.decode())))


def test_relationship_labels_are_separate_and_full_review_is_not_gold_completion(checkpoint):
    root, _, _, _, _, _, _, receipt, _ = checkpoint
    sample, rows = pilot_export(root, receipt)
    rows[0].update(label="left_contains_right", reviewer="Reviewer", notes="A contains B")
    rows[1].update(label="right_contains_left", reviewer="Reviewer", notes="B contains A")
    rows[2].update(label="related_scope_unclear", reviewer="Reviewer")
    rows[3].update(label="insufficient_evidence", reviewer="Reviewer")
    path = root / "pilot-export.csv"
    path.write_bytes(csv_bytes(rows, bom=True))
    before = (root / core.DIRECTORY / "original-decisions.csv").read_bytes()
    result = labels.validate_pilot_labels(path, sample)
    assert result["reviewed"] == 4 and result["unresolved"] == 18
    assert result["rows"] == rows and not result["formal_gold_set_complete"]
    assert (root / core.DIRECTORY / "original-decisions.csv").read_bytes() == before
    with pytest.raises(labels.TriageLabelError, match="unreviewed"):
        labels.validate_pilot_labels(path, sample, require_complete=True)
    for row in rows[4:]:
        row.update(label="insufficient_evidence", reviewer="Reviewer")
    path.write_bytes(csv_bytes(rows))
    result = labels.validate_pilot_labels(path, sample, require_complete=True)
    assert result["all_cases_reviewed"] and result["unresolved"] == 18
    assert result["formal_gold_set_complete"] is False


@pytest.mark.parametrize(
    "change",
    [
        {"sample_sha256": "b" * 64},
        {"left_record_id": "changed"},
        {"pair_id": "unknown"},
        {"label": "same_site", "reviewer": ""},
        {"label": "same_site "},
        {"label": "uncertain"},
    ],
)
def test_pilot_export_rejects_changed_identity_or_invalid_label(checkpoint, change):
    root, _, _, _, _, _, _, receipt, _ = checkpoint
    sample, rows = pilot_export(root, receipt)
    rows[0].update(change)
    path = root / "bad-export.csv"
    path.write_bytes(csv_bytes(rows))
    with pytest.raises(labels.TriageLabelError):
        labels.validate_pilot_labels(path, sample)


@pytest.mark.parametrize("kind", ["missing", "duplicate", "extra", "malformed"])
def test_pilot_export_requires_exact_twenty_rows(checkpoint, kind):
    root, _, _, _, _, _, _, receipt, _ = checkpoint
    sample, rows = pilot_export(root, receipt)
    if kind == "missing":
        rows.pop()
    elif kind == "duplicate":
        rows[-1] = dict(rows[0])
    elif kind == "extra":
        rows.append(dict(rows[0]))
    path = root / "bad-export.csv"
    content = csv_bytes(rows)
    path.write_bytes(content + b"bad,row\n" if kind == "malformed" else content)
    with pytest.raises(labels.TriageLabelError):
        labels.validate_pilot_labels(path, sample)


def test_changed_embedded_hash_or_pilot_details_fail_receipt_binding(checkpoint):
    root, _, _, _, _, _, _, receipt, _ = checkpoint
    path = root / core.DIRECTORY / "pilot.json"
    sample = json.loads(path.read_text())
    sample["sample_sha256"] = "b" * 64
    path.write_bytes(json_bytes(sample))
    with pytest.raises(labels.TriageLabelError, match="artifact changed"):
        labels.load_bound_sample(root, receipt)
    sample["cases"][0]["left"]["display_name"] = "Altered source name"
    sample["sample_sha256"] = labels.sample_digest(sample)
    path.write_bytes(json_bytes(sample))
    with pytest.raises(labels.TriageLabelError, match="artifact changed"):
        labels.load_bound_sample(root, receipt)
