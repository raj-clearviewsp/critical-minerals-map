import hashlib
import json

import pytest

from pipeline.release_products.common import QA, validate
from pipeline.release_products.quality import evidence_holds, known_date, source_registry


def test_qa_rejects_receipt_with_no_bindings(tmp_path):
    with pytest.raises(ValueError, match="Incomplete release input bindings"):
        validate(
            tmp_path,
            QA,
            {
                "release": "2026.11.04-v0",
                "status": "passed",
                "parents": {},
                "implementation": {},
                "artifacts": {},
                "counts": {},
            },
        )


def test_newer_raw_snapshot_cannot_substitute_for_accepted_source(tmp_path):
    src = tmp_path / "sources"
    src.mkdir()
    raw = tmp_path / "data/raw/test"
    (tmp_path / "reports").mkdir()
    (src / "test.yaml").write_text(
        json.dumps(
            {
                "source_id": "test",
                "name": "Test dataset",
                "publisher": "Test",
                "url": "https://example.org",
                "license": "CC0-1.0",
                "redistribution_ok": True,
                "license_status": "confirmed_open",
            }
        )
    )
    for day in ("2025-01-01", "2026-12-31"):
        p = raw / day / "manifest.json"
        p.parent.mkdir(parents=True)
        p.write_text(json.dumps({"retrieved_at": day}))
    accepted = raw / "2025-01-01/manifest.json"
    checksum = hashlib.sha256(accepted.read_bytes()).hexdigest()
    (tmp_path / "reports/normalization-audit.json").write_text(
        json.dumps(
            {
                "sources": [
                    {
                        "source_id": "test",
                        "files": {"test/2025-01-01/input.csv": "example"},
                        "manifest_sha256": checksum,
                    }
                ]
            }
        )
    )
    reg = source_registry(tmp_path)["test"]
    assert reg["retrieved_at"] == "2025-01-01" and reg["source_manifest_sha256"] == checksum
    accepted.write_text("{}")
    with pytest.raises(ValueError, match="snapshot manifest"):
        source_registry(tmp_path)


def test_row_permission_and_retrieval_date_are_checked():
    ev = {
        "source_id": "x",
        "source_url": "https://example.org/record",
        "retrieved_at": "2026-11-04",
        "evidence_grade": "B",
        "raw_sha256": "a" * 64,
        "redistribution_ok": False,
    }
    assert evidence_holds(ev, {"x": {"redistribution_ok": True}}) == {"source_terms"}
    assert not known_date("2026-11-04 fabricated")
