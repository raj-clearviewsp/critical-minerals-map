"""Staging preserves evidence and rejects incomplete identities and lost rows."""

import json
import sqlite3
from collections import Counter

import pyarrow.parquet as pq
import pytest

from pipeline.normalize.core import (
    ARROW_SCHEMA,
    NormalizeError,
    _write_batch,
    coordinate_pair,
    make_row,
    reconcile,
    validate_batch,
)

SOURCE = {"source_id": "fixture", "url": "https://publisher.example.test/", "evidence_grade": "B"}
MANIFEST = {"retrieved_at": None, "registered_at": "2026-10-31T00:00:00+00:00"}
FILE = "fixture/2026-10-31/data.csv"
FILES = {
    FILE: {
        "sha256": "a" * 64,
        "url": "https://publisher.example.test/data.csv",
        "retrieved_at": None,
    }
}


def row(**changes):
    source_row = {
        "native_id": "deposits:1",
        "source_file": FILE,
        "source_table": "deposits",
        "row_number": 2,
        "record_kind": "deposit",
        "raw_name": "A source name",
        "raw_fields": {"Ni": "0.5(?)", "Country": "historical label"},
        "raw_commodities": {"Commodity": "Ni(?)"},
        "lat": None,
        "lon": None,
    }
    source_row.update(changes)
    return make_row(SOURCE, MANIFEST, FILES, source_row)


def test_unlocated_and_unknown_retrieval_remain_explicit():
    result = row()
    assert result["record_id"] == "fixture:deposits:1"
    assert result["retrieved_at"] is None
    assert result["registered_at"] == MANIFEST["registered_at"]
    assert result["lat"] is result["lon"] is None
    assert result["location_precision"] == "none"
    assert json.loads(result["raw_fields"])["Ni"] == "0.5(?)"
    assert json.loads(result["raw_commodities"]) == {"Commodity": "Ni(?)"}
    assert validate_batch([result]).num_rows == 1


@pytest.mark.parametrize(
    "lat,lon", [(91, 0), (0, 181), ("bad", 2), (2, None), (float("nan"), 1), (0, 0)]
)
def test_invalid_location_keeps_evidence_and_becomes_unlocated(lat, lon):
    result = row(lat=lat, lon=lon, raw_fields={"lat": lat, "lon": lon})
    assert result["lat"] is result["lon"] is None
    assert result["coordinate_issue"]
    assert "lat" in json.loads(result["raw_fields"])
    validate_batch([result])


def test_no_accuracy_is_invented_from_coordinate_decimals():
    result = row(lat=12.345678, lon=43.567890)
    assert result["location_precision"] is None
    assert coordinate_pair(-90, 180) == (-90.0, 180.0, None)


def test_unmanifested_evidence_and_unknown_parser_fields_fail():
    with pytest.raises(NormalizeError, match="unmanifested"):
        row(source_file="fixture/2026-10-31/unverified.csv")
    with pytest.raises(NormalizeError, match="Unexpected parser fields"):
        row(maturity_rung=5)
    with pytest.raises(NormalizeError, match="Empty native"):
        row(native_id=" ")


def test_duplicate_record_identity_across_batches_fails(tmp_path):
    with sqlite3.connect(":memory:") as ids:
        ids.execute("CREATE TABLE ids (record_id TEXT PRIMARY KEY, source_row BLOB UNIQUE)")
        with pq.ParquetWriter(tmp_path / "rows.parquet", ARROW_SCHEMA) as writer:
            _write_batch(writer, ids, [row()])
            with pytest.raises(NormalizeError, match="Duplicate"):
                _write_batch(writer, ids, [row()])


def test_reconciliation_catches_lost_row_and_requires_reject_reason():
    profiles = [{"file": FILE, "table": "deposits", "rows": 2, "role": "primary"}]
    counts = Counter({(FILE, "deposits"): 1})
    with pytest.raises(NormalizeError, match="reconciliation"):
        reconcile(profiles, counts, [])
    rejection = {
        "source_file": FILE,
        "source_table": "deposits",
        "row_number": 3,
        "reason": "Explanatory footer, not a deposit",
        "raw_fields": {"text": "footnote"},
    }
    assert reconcile(profiles, counts, [rejection])[0]["rejected"] == 1
    rejection["reason"] = ""
    with pytest.raises(NormalizeError, match="reason"):
        reconcile(profiles, counts, [rejection])


def test_support_rows_do_not_inflate_site_records():
    profiles = [
        {"file": FILE, "table": "deposits", "rows": 1, "role": "primary"},
        {"file": FILE, "table": "references", "rows": 500, "role": "reference"},
    ]
    results = reconcile(profiles, Counter({(FILE, "deposits"): 1}), [])
    assert results[0]["accepted"] == 1
    assert "accepted" not in results[1]


def test_late_parser_failure_leaves_no_accepted_staging_files(tmp_path, monkeypatch):
    from pipeline.normalize import core

    monkeypatch.setattr(core, "check_vocab", lambda root: {"sources": {"a": {}, "b": {}}})
    monkeypatch.setattr(core, "REQUIRED_SOURCES", {"a", "b"})
    monkeypatch.setattr(core, "PARSERS", {"a": "fake", "b": "fake"})

    def stage(root, source, temp, expected):
        if source == "b":
            raise NormalizeError("Late parser error")
        (temp / "a.parquet").write_bytes(b"partial")
        return {"source_id": "a", "rows": 1, "rejects": []}

    monkeypatch.setattr(core, "stage_source", stage)
    with pytest.raises(NormalizeError, match="Late parser"):
        core.normalize(tmp_path)
    assert not (tmp_path / "data/staging").exists()
    assert not list((tmp_path / "data/.incoming").iterdir())


def test_same_source_row_with_a_different_id_still_fails(tmp_path):
    with sqlite3.connect(":memory:") as ids:
        ids.execute("CREATE TABLE ids (record_id TEXT PRIMARY KEY, source_row BLOB UNIQUE)")
        with pq.ParquetWriter(tmp_path / "rows.parquet", ARROW_SCHEMA) as writer:
            _write_batch(writer, ids, [row()])
            with pytest.raises(NormalizeError, match="physical source row"):
                _write_batch(writer, ids, [row(native_id="different")])


def test_final_write_failure_rolls_back_only_new_files(tmp_path, monkeypatch):
    from pipeline.normalize import core

    source = tmp_path / "incoming"
    target = tmp_path / "staging"
    source.mkdir()
    target.mkdir()
    staged = []
    for name in ["existing", "first", "last"]:
        (source / name).write_text(name)
        staged.append((source / name, target / name))
    (target / "existing").write_text("existing")
    original_link = core.os.link

    def link(old, new):
        if new.name == "last":
            raise OSError("Simulated disk failure")
        original_link(old, new)

    monkeypatch.setattr(core.os, "link", link)
    with pytest.raises(OSError, match="Simulated"):
        core.accept_outputs(staged)
    assert sorted(p.name for p in target.iterdir()) == ["existing"]


def test_unchanged_outputs_preserve_modification_time(tmp_path):
    from pipeline.normalize.core import accept_outputs

    original = tmp_path / "original"
    destination = tmp_path / "destination"
    original.write_bytes(b"reproducible")
    accept_outputs([(original, destination)])
    before = destination.stat().st_mtime_ns
    accept_outputs([(original, destination)])
    assert destination.stat().st_mtime_ns == before
    original.unlink()
    original.write_bytes(b"changed")
    with pytest.raises(NormalizeError, match="differs"):
        accept_outputs([(original, destination)])
    assert destination.read_bytes() == b"reproducible"


def test_retained_normalization_receipt_reconciles_without_raw_downloads():
    from pathlib import Path

    from pipeline.normalize.core import check

    receipt = check(Path(__file__).resolve().parents[1])
    assert receipt["canonical_rows"] == receipt["published_rows"] == 0
    assert receipt["rejects"] == sum(len(s["rejects"]) for s in receipt["sources"])
    for source in receipt["sources"]:
        assert source["rows"] == sum(source["kinds"].values())
        assert source["rows"] == sum(source["locations"].values())
        assert source["artifact"] == f"data/staging/{source['source_id']}.parquet"
