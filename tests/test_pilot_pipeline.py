"""Synthetic whole-pipeline checks for reconciliation and immutable acceptance."""

import json
import shutil
from collections import Counter
from pathlib import Path

import pyarrow as arrow
import pyarrow.parquet as parquet
import pytest
import yaml

from pipeline.normalize.core import ARROW_SCHEMA as PARENT_SCHEMA
from pipeline.normalize.core import NormalizeError, make_row, sha256
from pipeline.pilot import core

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def fixture_root(tmp_path, monkeypatch):
    shutil.copytree(ROOT / "sources/vocab", tmp_path / "sources/vocab")
    (tmp_path / "sources/pilot").mkdir()
    (tmp_path / "sources/pilot/expected_counts.yaml").write_text(
        yaml.safe_dump({"schema_version": "pilot-counts-v1", "sources": {"icmm": 2}})
    )
    (tmp_path / "uv.lock").write_text("synthetic pinned environment")
    (tmp_path / "reports").mkdir()
    (tmp_path / "reports/normalization-audit.json").write_text('{"synthetic":true}\n')
    path = tmp_path / "data/staging/icmm.parquet"
    path.parent.mkdir(parents=True)
    file = "icmm/2026-10-31/original.xlsx"
    rows = []
    for number, (commodity, country) in enumerate(
        [("Nickel", "Canada"), ("Copper", "Canada"), ("Cobalt", "Chile"), (None, None)], 1
    ):
        raw = {
            "native_id": str(number),
            "source_file": file,
            "source_table": "Facilities",
            "row_number": number,
            "record_kind": "facility",
            "country": country,
            "raw_commodities": {"Primary Commodity": commodity},
            "raw_fields": {"Country": country, "Original value": "  raw(?) ", "zero": 0},
        }
        if number == 1:
            raw["raw_commodities"]["Secondary Commodity "] = "unknown mineral"
        rows.append(
            make_row(
                {"source_id": "icmm", "url": "https://example.test/data", "evidence_grade": "B"},
                {"retrieved_at": None, "registered_at": "2026-10-31T00:00:00+00:00"},
                {file: {"sha256": "a" * 64, "retrieved_at": None}},
                raw,
            )
        )
    parquet.write_table(arrow.Table.from_pylist(rows, schema=PARENT_SCHEMA), path)
    parent = {
        "source_id": "icmm",
        "artifact": "data/staging/icmm.parquet",
        "rows": 4,
        "sha256": sha256(path),
        "kinds": {"facility": 4},
    }
    calls = []

    def parent_check(root, *, raw=False):
        calls.append(raw)
        return {"sources": [parent]}

    monkeypatch.setattr(core, "check_normalization", parent_check)
    monkeypatch.setattr(core, "PARSERS", {"icmm": "facilities"})
    monkeypatch.setattr(core.scope_review, "records", lambda root, vocab: [])
    monkeypatch.setattr(core, "BATCH_SIZE", 2)
    return tmp_path, rows, calls


def test_complete_filter_preserves_rows_and_reconciles_counts(fixture_root):
    root, original, calls = fixture_root
    before = sha256(root / "data/staging/icmm.parquet")
    progress = []
    result = core.filter_pilot(root, progress=progress.append)
    assert calls == [True]
    assert progress == result["sources"]
    assert (result["input_rows"], result["kept_rows"], result["excluded_rows"]) == (4, 2, 2)
    assert result["counts"] == {
        "commodity": {"Co": 1, "Ni": 1},
        "family": {"Co": 1, "Ni": 1},
        "host_country": {"CAN": 1, "CHL": 1},
        "record_kind": {"facility": 2},
    }
    assert result["exclusion_reasons"] == {"no_commodity_evidence": 1, "no_pilot_association": 1}
    assert result["review"]["record_term_occurrences"] == 1
    selected = parquet.read_table(root / result["sources"][0]["artifact"]).to_pylist()
    assert [{key: row[key] for key in PARENT_SCHEMA.names} for row in selected] == [
        original[0],
        original[2],
    ]
    assert sha256(root / "data/staging/icmm.parquet") == before
    assert core.check(root, raw=True) == result
    assert calls[-1] is True


def test_unchanged_filter_is_noop_and_conflicting_bytes_fail(fixture_root):
    root, _, _ = fixture_root
    first = core.filter_pilot(root)
    paths = [
        root / first["sources"][0]["artifact"],
        root / "reports/pilot-audit.json",
        root / "review/pilot-unmapped.csv",
        root / "review/pilot-scope-review.csv",
    ]
    mtimes = [path.stat().st_mtime_ns for path in paths]
    assert core.filter_pilot(root) == first
    assert [path.stat().st_mtime_ns for path in paths] == mtimes
    paths[0].write_bytes(b"conflict")
    with pytest.raises(NormalizeError, match="differs"):
        core.filter_pilot(root)
    assert paths[0].read_bytes() == b"conflict"
    assert json.loads(paths[1].read_text()) == first


def test_offline_check_needs_receipt_and_review_but_no_local_artifact(fixture_root):
    root, _, calls = fixture_root
    result = core.filter_pilot(root)
    (root / result["sources"][0]["artifact"]).unlink()
    assert core.check(root) == result
    assert calls[-1] is False
    with pytest.raises(FileNotFoundError):
        core.check(root, raw=True)


@pytest.mark.parametrize("change", ["grand_total", "source_total", "parent_hash", "kind_count"])
def test_receipt_reconciliation_rejects_tampering(fixture_root, change):
    root, _, _ = fixture_root
    result = core.filter_pilot(root)
    if change == "grand_total":
        result["input_rows"] += 1
    elif change == "source_total":
        result["sources"][0]["excluded_rows"] += 1
    elif change == "parent_hash":
        result["sources"][0]["input_sha256"] = "b" * 64
    else:
        result["sources"][0]["counts"]["record_kind"]["facility"] = 1
    (root / "reports/pilot-audit.json").write_text(json.dumps(result))
    with pytest.raises(core.PilotError):
        core.check(root)


def test_failed_release_count_gate_commits_nothing(fixture_root):
    root, _, _ = fixture_root
    (root / "sources/pilot/expected_counts.yaml").write_text(
        yaml.safe_dump({"schema_version": "pilot-counts-v1", "sources": {"icmm": 3}})
    )
    with pytest.raises(core.PilotError, match="release count"):
        core.filter_pilot(root)
    assert not (root / "data/staging/pilot").exists()
    assert not (root / "reports/pilot-audit.json").exists()
    assert not list((root / "data/.incoming").iterdir())


@pytest.mark.parametrize(
    "artifact",
    [*sorted(core.COUNT_REPORTS), "review/pilot-unmapped.csv", "review/pilot-scope-review.csv"],
)
def test_offline_check_rejects_changed_reports_and_review_artifacts(fixture_root, artifact):
    root, _, _ = fixture_root
    core.filter_pilot(root)
    path = root / artifact
    path.write_bytes(path.read_bytes() + b"changed\n")
    with pytest.raises(core.PilotError, match="differs"):
        core.check(root)


def test_review_counts_repeated_child_term_once_per_record(tmp_path):
    review = core.Review(tmp_path / "terms.sqlite")
    try:
        evidence = {"field": "Commods", "raw_value": "Ni + unknown", "unmapped": ["unknown"]}
        review.add("fixture", [evidence, evidence])
        review.add("fixture", [evidence])
        result = review.write(tmp_path / "review.csv")
        assert result["distinct_terms"] == 1
        assert result["record_term_occurrences"] == 2
    finally:
        review.close()


def test_counts_do_not_duplicate_repeated_codes_or_countries():
    counts = {name: Counter() for name in core.DIMENSIONS}
    core._count_row(
        counts,
        {
            "pilot_commodities": '["Ni","Ni","Co"]',
            "pilot_families": '["Ni","Co"]',
            "country_iso3s": '["ZMB","ZMB"]',
            "record_kind": "finance",
        },
    )
    assert counts["commodity"] == {"Ni": 1, "Co": 1}
    assert counts["host_country"] == {"ZMB": 1}
