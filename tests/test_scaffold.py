"""Acceptance checks for the empty-input scaffold and its preservation boundaries."""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pandas as pd
import pytest

from pipeline.qa.schemas import TABLE_FIELDS, empty_table, validate_empty_table
from pipeline.scaffold import (
    STAGES,
    ScaffoldError,
    run_stage,
    validate_manifest,
    validate_release_tag,
    write_preserving,
)

RELEASE = "2026.10.28"
REPO = Path(__file__).resolve().parents[1]


def build_through(root: Path, last: str = "publish") -> None:
    for stage in STAGES[: STAGES.index(last) + 1]:
        run_stage(root, stage, RELEASE)


def file_snapshot(root: Path) -> dict[str, tuple[bytes, int]]:
    return {
        str(path.relative_to(root)): (path.read_bytes(), path.stat().st_mtime_ns)
        for path in root.rglob("*")
        if path.is_file()
    }


def write_file(root: Path, relative: str, payload: bytes) -> Path:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    return path


def test_empty_pipeline_builds_valid_products_and_rerun_changes_nothing(tmp_path: Path) -> None:
    raw_marker = write_file(tmp_path, "data/raw/.gitkeep", b"")
    override = write_file(tmp_path, "overrides/reviewed.yaml", b"reviewer: Example reviewer\n")

    build_through(tmp_path)
    manifest = validate_manifest(tmp_path / "data/publish/manifest.json")
    assert manifest["status"] == "scaffold"
    assert manifest["data_as_of"] is None
    assert manifest["sources"] == []
    assert manifest["release_tag"] == RELEASE
    assert set(manifest["tables"]) == set(TABLE_FIELDS)
    assert not list(tmp_path.rglob("*.pmtiles"))

    for name, metadata in manifest["tables"].items():
        assert metadata["rows"] == 0
        canonical = pd.read_parquet(tmp_path / f"data/canonical/{name}.parquet")
        validate_empty_table(name, canonical)
        assert len(canonical) == 0
        for extension in ("csv", "parquet"):
            output = tmp_path / "data/publish" / metadata[extension]
            assert (
                hashlib.sha256(output.read_bytes()).hexdigest() == metadata[f"{extension}_sha256"]
            )
        csv = pd.read_csv(tmp_path / "data/publish" / metadata["csv"])
        assert list(csv.columns) == list(TABLE_FIELDS[name])
        assert len(csv) == 0

    for stage in STAGES:
        receipt = json.loads((tmp_path / f"reports/runs/{stage}.json").read_text())
        assert receipt["stage"] == stage
        assert receipt["rows"] == receipt["rejects"] == 0
        assert receipt["status"] == "scaffold_empty_pass"

    before = file_snapshot(tmp_path)
    build_through(tmp_path)
    assert file_snapshot(tmp_path) == before
    assert raw_marker.read_bytes() == b""
    assert override.read_bytes() == b"reviewer: Example reviewer\n"


@pytest.mark.parametrize("stage", STAGES[1:])
def test_stages_require_predecessor_receipt(tmp_path: Path, stage: str) -> None:
    with pytest.raises(ScaffoldError, match="prerequisite"):
        run_stage(tmp_path, stage, RELEASE)
    assert file_snapshot(tmp_path) == {}


@pytest.mark.parametrize(
    "relative",
    [
        "data/raw/example/2026-10-28/download.csv",
        "sources/example.yaml",
        "sources/example.yml",
        "data/staging/unrecognized.csv",
        "data/canonical/unrecognized.parquet",
        "data/publish/private/source.json",
    ],
)
def test_unimplemented_inputs_stop_without_mutation(tmp_path: Path, relative: str) -> None:
    write_file(tmp_path, relative, b"source input must be preserved\n")
    write_file(tmp_path, "overrides/approved.yaml", b"reviewer: Raj\n")
    before = file_snapshot(tmp_path)
    with pytest.raises(ScaffoldError):
        run_stage(tmp_path, "fetch", RELEASE)
    assert file_snapshot(tmp_path) == before


@pytest.mark.parametrize(
    ("area", "table"),
    [("staging", "source_record"), ("canonical", "site"), ("publish", "site")],
)
def test_populated_parquet_cannot_enter_scaffold(tmp_path: Path, area: str, table: str) -> None:
    path = tmp_path / f"data/{area}/{table}.parquet"
    path.parent.mkdir(parents=True)
    # Even a row of missing values is a real input, never an empty build.
    frame = empty_table(table).reindex([0])
    frame.to_parquet(path, index=False)
    before = file_snapshot(tmp_path)
    with pytest.raises(ScaffoldError, match="nonempty"):
        run_stage(tmp_path, "fetch", RELEASE)
    assert file_snapshot(tmp_path) == before


@pytest.mark.parametrize("area", ["staging", "canonical", "publish"])
def test_corrupt_parquet_stops_without_replacing_it(tmp_path: Path, area: str) -> None:
    table = "source_record" if area == "staging" else "site"
    write_file(tmp_path, f"data/{area}/{table}.parquet", b"not parquet")
    before = file_snapshot(tmp_path)
    with pytest.raises(ScaffoldError):
        run_stage(tmp_path, "fetch", RELEASE)
    assert file_snapshot(tmp_path) == before


def test_populated_published_csv_is_preserved_and_rejected(tmp_path: Path) -> None:
    payload = empty_table("site").to_csv(index=False).encode() + b"unreviewed record\n"
    write_file(tmp_path, "data/publish/site.csv", payload)
    before = file_snapshot(tmp_path)
    with pytest.raises(ScaffoldError, match="published CSV"):
        run_stage(tmp_path, "fetch", RELEASE)
    assert file_snapshot(tmp_path) == before


@pytest.mark.parametrize(
    ("build_to", "missing", "next_stage"),
    [
        ("normalize", "data/staging/source_record.parquet", "harmonize"),
        ("harmonize", "data/canonical/site.parquet", "link"),
        ("enrich", "data/canonical/source.parquet", "qa"),
    ],
)
def test_deleted_upstream_artifact_blocks_next_stage(
    tmp_path: Path, build_to: str, missing: str, next_stage: str
) -> None:
    build_through(tmp_path, build_to)
    (tmp_path / missing).unlink()
    before = file_snapshot(tmp_path)
    with pytest.raises(ScaffoldError, match="prerequisite"):
        run_stage(tmp_path, next_stage, RELEASE)
    assert file_snapshot(tmp_path) == before


@pytest.mark.parametrize(
    ("field", "value"),
    [("rows", 1), ("status", "failed"), ("stage", "qa"), ("release_tag", "2026.10.27")],
)
def test_invalid_receipt_blocks_downstream_stage(tmp_path: Path, field: str, value: object) -> None:
    build_through(tmp_path, "fetch")
    receipt_path = tmp_path / "reports/runs/fetch.json"
    receipt = json.loads(receipt_path.read_text())
    receipt[field] = value
    receipt_path.write_text(json.dumps(receipt))
    with pytest.raises(ScaffoldError, match="prerequisite"):
        run_stage(tmp_path, "normalize", RELEASE)
    assert not (tmp_path / "data/staging/source_record.parquet").exists()


def test_corrupt_receipt_blocks_downstream_stage(tmp_path: Path) -> None:
    write_file(tmp_path, "reports/runs/fetch.json", b"{broken json")
    with pytest.raises(ScaffoldError, match="prerequisite"):
        run_stage(tmp_path, "normalize", RELEASE)


def test_receipt_missing_required_artifact_hash_blocks_downstream_stage(tmp_path: Path) -> None:
    build_through(tmp_path, "normalize")
    receipt_path = tmp_path / "reports/runs/normalize.json"
    receipt = json.loads(receipt_path.read_text())
    receipt["artifacts"] = {}
    receipt_path.write_text(json.dumps(receipt))
    before = file_snapshot(tmp_path)
    with pytest.raises(ScaffoldError, match="prerequisite"):
        run_stage(tmp_path, "harmonize", RELEASE)
    assert file_snapshot(tmp_path) == before


@pytest.mark.parametrize("failure", ["checksum", "missing", "corrupt", "wrong-path"])
def test_manifest_rejects_tampered_or_missing_products(tmp_path: Path, failure: str) -> None:
    build_through(tmp_path)
    manifest_path = tmp_path / "data/publish/manifest.json"
    if failure == "missing":
        (manifest_path.parent / "site.csv").unlink()
    elif failure == "corrupt":
        manifest_path.write_text("{broken json")
    else:
        manifest = json.loads(manifest_path.read_text())
        key = "csv_sha256" if failure == "checksum" else "csv"
        manifest["tables"]["site"][key] = "0" * 64 if failure == "checksum" else "source.csv"
        manifest_path.write_text(json.dumps(manifest))
    with pytest.raises(ScaffoldError):
        validate_manifest(manifest_path)


@pytest.mark.parametrize("tag", ["2026.02.29", "2026.13.01", "2026.1.01", "26.01.01", "today"])
def test_release_tags_require_real_zero_padded_dates(tmp_path: Path, tag: str) -> None:
    with pytest.raises(ScaffoldError, match="Release tag"):
        run_stage(tmp_path, "fetch", tag)
    assert file_snapshot(tmp_path) == {}


def test_leap_day_is_valid_in_a_leap_year() -> None:
    validate_release_tag("2028.02.29")


def test_new_release_cannot_silently_replace_previous_receipts(tmp_path: Path) -> None:
    build_through(tmp_path, "fetch")
    before = file_snapshot(tmp_path)
    with pytest.raises(ScaffoldError, match="overwrite"):
        run_stage(tmp_path, "fetch", "2026.10.29")
    assert file_snapshot(tmp_path) == before


def test_conflict_preflight_leaves_all_outputs_untouched(tmp_path: Path) -> None:
    write_file(tmp_path, "data/publish/existing.csv", b"preserve")
    before = file_snapshot(tmp_path)
    with pytest.raises(ScaffoldError, match="overwrite"):
        write_preserving(
            tmp_path,
            {"data/publish/new.csv": b"new", "data/publish/existing.csv": b"replacement"},
        )
    assert file_snapshot(tmp_path) == before


def test_unknown_cli_stage_exits_nonzero_without_files(tmp_path: Path) -> None:
    result = subprocess.run(
        [sys.executable, "-m", "pipeline", "--root", str(tmp_path), "unknown-stage"],
        cwd=REPO,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 2
    assert "invalid choice" in result.stderr
    assert file_snapshot(tmp_path) == {}


def test_cli_reports_blocked_raw_input_without_traceback(tmp_path: Path) -> None:
    write_file(tmp_path, "data/raw/source/2026-10-28/data.csv", b"source input\n")
    before = file_snapshot(tmp_path)
    result = subprocess.run(
        [sys.executable, "-m", "pipeline", "--root", str(tmp_path), "fetch"],
        cwd=REPO,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 2
    assert "Atlas stopped:" in result.stderr
    assert "Traceback" not in result.stderr
    assert file_snapshot(tmp_path) == before
