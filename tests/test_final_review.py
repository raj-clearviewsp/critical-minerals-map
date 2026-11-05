import copy
import csv
import io
import json
import re

import pytest
from test_research_review import make_case, proposal

from pipeline.final_review import core
from pipeline.scaffold import json_bytes


@pytest.fixture
def packet(tmp_path, monkeypatch):
    root = tmp_path / "atlas"
    (root / "reports").mkdir(parents=True)
    (root / "data/review/linkage").mkdir(parents=True)
    (root / "experiments/matching").mkdir(parents=True)
    (root / "reports/reconciliation-audit.json").write_bytes(
        json_bytes({"packets": {"holdout": {"cases": 80, "sample_sha256": "a" * 64}}})
    )
    (root / core.COMPLETED_RECEIPT).write_text("{}")
    (root / core.PROTOCOL).write_text('{"authorization":"final47 now assisted"}')
    (root / "data/review/linkage/sample.json").write_text('{"source_names":{}}')
    original = {"cases": [make_case(n) for n in range(1, 81)], "sample_sha256": "a" * 64}
    proposals = []
    for n, i in enumerate(range(34, 81), 1):
        p = proposal(i)
        p["case_number"] = n
        proposals.append(p)
    research = {"schema_version": "researched-assisted47-v1", "cases": proposals}
    path = tmp_path / "research.json"
    path.write_bytes(json_bytes(research))
    monkeypatch.setattr(
        core, "check_completed", lambda *a, **kw: {"review": {"formal_human_reviewed": 153}}
    )
    monkeypatch.setattr(core, "load_packet", lambda *a, **kw: copy.deepcopy(original))
    monkeypatch.setattr(core, "implementation_hashes", lambda r: {"synthetic": "b" * 64})
    return root, path, original, research


def test_final47_exact_selection_blank_answers_and_changed_evaluation_role(packet):
    root, path, original, _ = packet
    outputs, receipt = core.build(root, path)
    sample = json.loads(outputs[f"{core.DIRECTORY}/sample.json"])
    assert sample["mode"] == "adjudication"
    assert sample["original_split"] == "holdout"
    assert sample["evaluation_role"] == "agent_assisted_audit_not_blind"
    assert [{k: v for k, v in c.items() if k != "research"} for c in sample["cases"]] == [
        {k: v for k, v in c.items() if k != "research"} for c in original["cases"][33:]
    ]
    rows = list(
        csv.DictReader(io.StringIO(outputs[f"{core.DIRECTORY}/decisions-blank.csv"].decode()))
    )
    assert len(rows) == 47 and all(not r["label"] and not r["reviewer"] for r in rows)
    assert receipt["new_human_labels"] == 0 and receipt["blind_evaluation_cases"] == 0
    page = outputs[f"{core.DIRECTORY}/index.html"]
    payload = json.loads(
        re.search(rb'<script id="review-data" type="application/json">(.*?)</script>', page)[1]
    )
    assert len(payload["cases"]) == 47
    assert payload["metadata"]["review_role"] == "agent_assisted_audit_not_blind"
    assert b"final47:assisted:v1:" in page and b"atlas-final47-assisted-" in page
    assert b"all 60 analyses" not in page and b"your answers start blank" in page
    assert b"no longer a blind" not in page or b"longer a blind evaluation set" in page
    assert outputs[f"{core.DIRECTORY}/analysis.html"].count(b'<article id="case-') == 47
    assert b'href="index.html#case-47"' in outputs[f"{core.DIRECTORY}/analysis.html"]


@pytest.mark.parametrize(
    "mutation", ["order", "wrong_size", "missing_peer", "bad_url", "changed_no_explanation"]
)
def test_final_research_cannot_bypass_evidence_validation(packet, mutation):
    root, path, _, research = packet
    if mutation == "order":
        research["cases"].reverse()
    elif mutation == "wrong_size":
        research["cases"].pop()
    elif mutation == "missing_peer":
        research["cases"][0].pop("second_review")
    elif mutation == "bad_url":
        research["cases"][0]["citations"][0]["url"] = "javascript:alert(1)"
    else:
        research["cases"][0]["initial_recommendation"] = "different_sites"
        research["cases"][0].pop("editorial_review", None)
    path.write_bytes(json_bytes(research))
    with pytest.raises(ValueError):
        core.build(root, path)


def test_final_packet_is_immutable_and_offline_receipt_binds_implementation(packet):
    root, path, _, research = packet
    receipt = core.prepare(root, path)
    paths = [p for p in (root / core.DIRECTORY).iterdir()]
    times = {p: p.stat().st_mtime_ns for p in paths}
    assert core.prepare(root) == receipt
    assert all(p.stat().st_mtime_ns == t for p, t in times.items())
    assert core.check(root, local=True) == receipt
    research["cases"][0]["summary"] = "Changed content after acceptance"
    path.write_bytes(json_bytes(research))
    with pytest.raises(ValueError):
        core.prepare(root, path)


def test_export_is_bound_to_new_assisted_packet(packet, tmp_path):
    root, path, _, _ = packet
    receipt = core.prepare(root, path)
    sample = json.loads((root / core.DIRECTORY / "sample.json").read_text())
    csv_path = root / core.DIRECTORY / "decisions-blank.csv"
    assert core.validate_export(csv_path, sample)["counts"] == {"blank": 47}
    raw = csv_path.read_text().replace(receipt["sample_sha256"], "f" * 64)
    altered = tmp_path / "wrong.csv"
    altered.write_text(raw)
    with pytest.raises(ValueError):
        core.validate_export(altered, sample)


def test_offline_check_rejects_false_human_approval(packet):
    root, path, _, _ = packet
    receipt = core.prepare(root, path)
    receipt["status"] = "explicit_human_approval_recorded"
    (root / core.RECEIPT).write_bytes(json_bytes(receipt))
    with pytest.raises(ValueError, match="Invalid final47 receipt"):
        core.check(root)


@pytest.mark.parametrize(
    "field",
    [
        "completed_receipt_sha256",
        "parent_receipt_sha256",
        "original_holdout_sample_sha256",
        "sample_sha256",
        "protocol_sha256",
    ],
)
def test_offline_check_rejects_malformed_digest_fields(packet, field):
    root, path, _, _ = packet
    receipt = core.prepare(root, path)
    receipt[field] = "not-a-hash"
    (root / core.RECEIPT).write_bytes(json_bytes(receipt))
    with pytest.raises(ValueError, match="Invalid final47 checksum"):
        core.check(root)


def test_offline_check_binds_original_holdout_hash_to_parent(packet):
    root, path, _, _ = packet
    receipt = core.prepare(root, path)
    receipt["original_holdout_sample_sha256"] = "f" * 64
    (root / core.RECEIPT).write_bytes(json_bytes(receipt))
    # The aggregate-only check must catch a valid-looking but incorrect hash
    # without accessing the private source packet.
    (root / core.DIRECTORY / "sample.json").unlink()
    with pytest.raises(ValueError, match="Final47 code, protocol, parent or report changed"):
        core.check(root)


@pytest.mark.parametrize(
    ("keys", "value"),
    [
        (("new_human_labels",), False),
        (("blind_evaluation_cases",), 1),
        (("auto_merges",), 0.0),
        (("human_reviewed_formal_units",), 152),
        (("research", "recommendation_counts"), {"same_site": 48, "different_sites": -1}),
        (("research", "recommendation_counts"), {"same_site": 46.0, "different_sites": 1}),
        (("research", "recommendation_counts"), {"unknown_label": 47}),
        (("research", "confidence_counts"), {"medium": 46}),
        (("research", "second_review_outcomes"), {"unknown_outcome": 47}),
        (("research", "citation_count"), 46),
        (("research", "unique_citation_urls"), 0),
        (("research", "unique_citation_urls"), 48),
        (("research", "recommendations_changed_after_review"), 48),
        (("research", "citation_access_counts"), {"local_snapshot": 47}),
        (("research", "citation_access_counts"), {"unknown_access": 47}),
    ],
)
def test_receipt_rejects_invalid_or_inconsistent_counts(packet, keys, value):
    root, path, _, _ = packet
    _, receipt = core.build(root, path)
    target = receipt
    for key in keys[:-1]:
        target = target[key]
    target[keys[-1]] = value
    with pytest.raises(ValueError):
        core.validate_receipt(receipt)


@pytest.mark.parametrize("mutation", ["artifact_hash", "artifact_size", "implementation_hash"])
def test_receipt_validates_artifact_and_implementation_checksums(packet, mutation):
    root, path, _, _ = packet
    _, receipt = core.build(root, path)
    if mutation == "artifact_hash":
        receipt["artifacts"][f"{core.DIRECTORY}/sample.json"]["sha256"] = "A" * 64
    elif mutation == "artifact_size":
        receipt["artifacts"][f"{core.DIRECTORY}/sample.json"]["bytes"] = True
    else:
        receipt["implementation"]["synthetic"] = None
    with pytest.raises(ValueError):
        core.validate_receipt(receipt)
