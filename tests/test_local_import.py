"""Original browser files retain byte identity and honestly incomplete provenance."""

from __future__ import annotations

import hashlib
import io
import json
import zipfile
from datetime import UTC, datetime, timedelta

import pytest
import yaml

from pipeline.fetch.core import FetchError, verify_snapshot
from pipeline.fetch.local import import_local, load_intake

NOW = datetime(2026, 10, 30, 12, tzinfo=UTC)
URL = "https://publisher.example.test/data.csv"
CONTENT = b"id,name\n1,Synthetic fixture\n"


def setup(root, **changes):
    source = {
        "source_id": "test_source",
        "name": "Synthetic test fixture",
        "publisher": "Test publisher",
        "url": "https://publisher.example.test/",
        "license": "CC0-1.0",
        "redistribution_ok": True,
        "license_status": "confirmed_open",
        "license_evidence_url": "https://publisher.example.test/license",
        "access_status": "manual_required",
        "cadence": "manual",
        "parser": "not_implemented",
        "fetcher": {"kind": "disabled", "files": []},
    }
    source.update(changes)
    (root / "sources").mkdir(exist_ok=True)
    (root / "sources/test_source.yaml").write_text(yaml.safe_dump(source))
    local = root / "download.csv"
    local.write_bytes(CONTENT)
    return {
        "source_id": "test_source",
        "acquisition_method": "user_existing_download",
        "files": [
            {
                "local_path": str(local),
                "filename": "data.csv",
                "url": URL,
                "retrieved_at": None,
            }
        ],
    }


def test_unknown_retrieval_is_not_invented_and_original_is_untouched(tmp_path):
    intake = setup(tmp_path)
    original = tmp_path / "download.csv"
    mtime = original.stat().st_mtime_ns
    result = import_local(tmp_path, intake, now=NOW)
    target = tmp_path / "data/raw/test_source/2026-10-30"
    manifest = verify_snapshot(target)
    assert result["status"] == "imported"
    assert manifest["schema_version"] == "raw-v2"
    assert manifest["retrieved_at"] is None
    assert manifest["registered_at"] == NOW.isoformat()
    assert manifest["files"][0]["retrieved_at"] is None
    assert manifest["files"][0]["final_url"] is None
    assert str(tmp_path) not in json.dumps(manifest)
    assert (target / "data.csv").read_bytes() == original.read_bytes() == CONTENT
    assert original.stat().st_mtime_ns == mtime


def test_browser_retrieval_is_recorded_separately_from_registration(tmp_path):
    intake = setup(tmp_path)
    retrieved = (NOW - timedelta(hours=2)).isoformat()
    intake["acquisition_method"] = "browser_download"
    intake["files"][0].update(
        retrieved_at=retrieved,
        final_url="https://publisher.example.test/download/data.csv",
        retrieval_evidence="Observed browser download completion time in UTC.",
    )
    result = import_local(tmp_path, intake, now=NOW)
    assert result["manifest"]["retrieved_at"] == retrieved
    assert result["manifest"]["registered_at"] == NOW.isoformat()


@pytest.mark.parametrize(
    "changes",
    [
        {"retrieved_at": NOW.replace(tzinfo=None).isoformat()},
        {"retrieved_at": (NOW + timedelta(days=1)).isoformat()},
        {"retrieved_at": NOW.isoformat()},
        {"filename": "../data.csv"},
        {"filename": "MANIFEST.json"},
        {"url": "https://secret:password@publisher.example.test/data.csv"},
    ],
)
def test_untrustworthy_provenance_is_rejected(tmp_path, changes):
    intake = setup(tmp_path)
    intake["files"][0].update(changes)
    with pytest.raises(FetchError):
        import_local(tmp_path, intake, now=NOW)
    assert not list((tmp_path / "data/raw").glob("*/*"))


def test_identical_rerun_retains_manifest_and_mtime(tmp_path):
    intake = setup(tmp_path)
    result = import_local(tmp_path, intake, now=NOW)
    target = tmp_path / "data/raw/test_source/2026-10-30"
    before = {path.name: (path.read_bytes(), path.stat().st_mtime_ns) for path in target.iterdir()}
    intake["files"][0]["retrieved_at"] = NOW.isoformat()
    intake["files"][0]["retrieval_evidence"] = "Later confirmation of browser acquisition time."
    again = import_local(tmp_path, intake, now=NOW + timedelta(days=1))
    assert again["status"] == "unchanged"
    assert again["manifest"] == result["manifest"]
    assert {
        path.name: (path.read_bytes(), path.stat().st_mtime_ns) for path in target.iterdir()
    } == before
    assert not (tmp_path / "data/.incoming").exists()


def test_changed_same_day_preserves_snapshot_and_cleans_partial_copy(tmp_path):
    intake = setup(tmp_path)
    import_local(tmp_path, intake, now=NOW)
    (tmp_path / "download.csv").write_bytes(b"id,name\n2,Changed fixture\n")
    with pytest.raises(FetchError, match="existing daily snapshot"):
        import_local(tmp_path, intake, now=NOW)
    assert (tmp_path / "data/raw/test_source/2026-10-30/data.csv").read_bytes() == CONTENT
    assert not (tmp_path / "data/.incoming").exists()


@pytest.mark.parametrize("checksum_type", ["string", "object"])
def test_publisher_checksum_and_bytes_are_verified(tmp_path, checksum_type):
    value = hashlib.md5(CONTENT).hexdigest()
    checksum = f"md5:{value}" if checksum_type == "string" else {"type": "MD5", "value": value}
    intake = setup(
        tmp_path,
        candidate_files=[
            {
                "filename": "data.csv",
                "url": URL,
                "publisher_checksum": checksum,
                "bytes": len(CONTENT),
            }
        ],
    )
    assert import_local(tmp_path, intake, now=NOW)["status"] == "imported"
    (tmp_path / "download.csv").write_bytes(b"x" * len(CONTENT))
    with pytest.raises(FetchError, match="Publisher checksum mismatch"):
        import_local(tmp_path, intake, now=NOW + timedelta(days=1))


def test_partial_registered_file_set_is_rejected(tmp_path):
    intake = setup(
        tmp_path,
        fetcher={
            "kind": "files",
            "files": [
                {"filename": "data.csv", "url": URL},
                {"filename": "references.csv", "url": URL},
            ],
        },
    )
    with pytest.raises(FetchError, match="missing registered files: references.csv"):
        import_local(tmp_path, intake, now=NOW)


@pytest.mark.parametrize("name", ["data.csv", "data.xlsx", "data.zip", "data.pdf"])
def test_browser_error_page_cannot_be_registered_as_data(tmp_path, name):
    intake = setup(tmp_path)
    (tmp_path / "download.csv").write_bytes(b"<!DOCTYPE html><html>Download failed</html>")
    intake["files"][0]["filename"] = name
    with pytest.raises(FetchError, match="HTML error page"):
        import_local(tmp_path, intake, now=NOW)
    assert not (tmp_path / "data/.incoming").exists()


def test_unapproved_license_and_excluded_source_fail_before_copy(tmp_path):
    intake = setup(tmp_path, license_status="unconfirmed", redistribution_ok=False)
    with pytest.raises(FetchError, match="license not cleared"):
        import_local(tmp_path, intake, now=NOW)
    (tmp_path / "overrides").mkdir()
    approval = {
        "approved_by": "Synthetic project owner",
        "approved_at": "2026-10-30",
        "decision": "allow_acquisition",
        "reason": "Synthetic acquisition-only decision",
    }
    (tmp_path / "overrides/source_acquisition_approvals.yaml").write_text(
        yaml.safe_dump({"approvals": {"test_source": approval}})
    )
    result = import_local(tmp_path, intake, now=NOW)
    assert result["manifest"]["redistribution_ok"] is False
    assert result["manifest"]["acquisition_approval"] == approval
    setup(tmp_path, access_status="excluded")
    with pytest.raises(FetchError, match="excluded"):
        import_local(tmp_path, intake, now=NOW)


def test_corrupted_snapshot_blocks_rerun(tmp_path):
    intake = setup(tmp_path)
    import_local(tmp_path, intake, now=NOW)
    (tmp_path / "data/raw/test_source/2026-10-30/data.csv").write_bytes(b"modified")
    with pytest.raises(FetchError, match="checksum"):
        import_local(tmp_path, intake, now=NOW)


def test_intake_relative_paths_resolve_beside_intake(tmp_path):
    intake = setup(tmp_path)
    intake["files"][0]["local_path"] = "download.csv"
    path = tmp_path / "intake.json"
    path.write_text(json.dumps(intake))
    loaded = load_intake(path)
    assert loaded["files"][0]["local_path"] == str(tmp_path / "download.csv")
    assert import_local(tmp_path, loaded, now=NOW)["status"] == "imported"


def test_snapshot_cannot_claim_unknown_file_retrieval_is_known(tmp_path):
    intake = setup(tmp_path)
    import_local(tmp_path, intake, now=NOW)
    directory = tmp_path / "data/raw/test_source/2026-10-30"
    path = directory / "manifest.json"
    manifest = json.loads(path.read_text())
    manifest["retrieved_at"] = NOW.isoformat()
    path.write_text(json.dumps(manifest))
    with pytest.raises(FetchError, match="does not match file provenance"):
        verify_snapshot(directory)


def test_cli_import_needs_no_contact_or_http_client(tmp_path, monkeypatch, capsys):
    from pipeline.fetch import __main__ as cli

    intake = setup(tmp_path)
    path = tmp_path / "intake.json"
    path.write_text(json.dumps(intake))
    monkeypatch.setattr("sys.argv", ["fetch", "--root", str(tmp_path), "--import-local", str(path)])
    monkeypatch.delenv("ATLAS_CONTACT", raising=False)

    def unexpected_http(*args, **kwargs):
        pytest.fail("Local import must never construct an HTTP client")

    monkeypatch.setattr(cli, "PoliteHTTP", unexpected_http)
    assert cli.main() == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary["status"] == "imported"
    assert summary["bytes"] == len(CONTENT)
    reports = list((tmp_path / "reports/fetch").glob("test_source-*.json"))
    assert len(reports) == 1
    assert json.loads(reports[0].read_text())["row_count"] is None


def test_naive_registration_time_cannot_pass_snapshot_verification(tmp_path):
    intake = setup(tmp_path)
    import_local(tmp_path, intake, now=NOW)
    directory = tmp_path / "data/raw/test_source/2026-10-30"
    path = directory / "manifest.json"
    manifest = json.loads(path.read_text())
    manifest["registered_at"] = NOW.replace(tzinfo=None).isoformat()
    path.write_text(json.dumps(manifest))
    with pytest.raises(FetchError, match="timezone"):
        verify_snapshot(directory)


def test_weaker_candidate_cannot_erase_publisher_hash(tmp_path):
    intake = setup(
        tmp_path,
        fetcher={
            "kind": "files",
            "files": [
                {"filename": "data.csv", "url": URL, "publisher_checksum": "sha256:" + "0" * 64}
            ],
        },
        candidate_files=[{"filename": "data.csv", "url": URL}],
    )
    with pytest.raises(FetchError, match="Publisher checksum mismatch"):
        import_local(tmp_path, intake, now=NOW)


@pytest.mark.parametrize("constraint", ["bytes", "checksum"])
def test_conflicting_duplicate_constraints_fail_before_acceptance(tmp_path, constraint):
    first = {"filename": "data.csv", "url": URL}
    second = dict(first)
    if constraint == "bytes":
        first["publisher_bytes"] = 12
        second["bytes"] = 13
    else:
        first["publisher_checksum"] = "md5:" + "0" * 32
        second["publisher_checksum"] = {"type": "MD5", "value": "1" * 32}
    intake = setup(tmp_path, fetcher={"kind": "files", "files": [first]}, candidate_files=[second])
    with pytest.raises(FetchError, match="Conflicting publisher"):
        import_local(tmp_path, intake, now=NOW)


def test_different_publisher_hash_algorithms_both_apply(tmp_path):
    intake = setup(
        tmp_path,
        fetcher={
            "kind": "files",
            "files": [
                {
                    "filename": "data.csv",
                    "url": URL,
                    "publisher_checksum": "sha256:" + hashlib.sha256(CONTENT).hexdigest(),
                }
            ],
        },
        candidate_files=[
            {"filename": "data.csv", "url": URL, "publisher_checksum": "md5:" + "0" * 32}
        ],
    )
    with pytest.raises(FetchError, match="Publisher checksum mismatch"):
        import_local(tmp_path, intake, now=NOW)


def test_zip_with_valid_directory_but_bad_member_crc_is_rejected(tmp_path):
    intake = setup(tmp_path)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_STORED) as archive:
        archive.writestr("fixture.txt", b"Original contents")
    corrupt = buffer.getvalue().replace(b"Original contents", b"Modified contents")
    with zipfile.ZipFile(io.BytesIO(corrupt)) as archive:
        assert archive.namelist() == ["fixture.txt"]
    (tmp_path / "download.csv").write_bytes(corrupt)
    intake["files"][0]["filename"] = "data.zip"
    with pytest.raises(FetchError, match="Invalid or incomplete ZIP"):
        import_local(tmp_path, intake, now=NOW)
    assert not (tmp_path / "data/.incoming").exists()


def test_archive_expansion_limit_prevents_accepting_oversized_members(tmp_path, monkeypatch):
    intake = setup(tmp_path)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("fixture.txt", b"x" * 1024)
    (tmp_path / "download.csv").write_bytes(buffer.getvalue())
    intake["files"][0]["filename"] = "data.zip"
    monkeypatch.setattr("pipeline.fetch.local.MAX_EXPANDED_BYTES", 512)
    with pytest.raises(FetchError, match="Archive expands beyond"):
        import_local(tmp_path, intake, now=NOW)
    assert not (tmp_path / "data/.incoming").exists()


def test_unchanged_bytes_flag_changed_registry_without_rewriting_history(tmp_path):
    intake = setup(tmp_path)
    first = import_local(tmp_path, intake, now=NOW)
    registry = tmp_path / "sources/test_source.yaml"
    source = yaml.safe_load(registry.read_text())
    source["license"] = "CC-BY-4.0"
    registry.write_text(yaml.safe_dump(source))
    second = import_local(tmp_path, intake, now=NOW)
    assert second["status"] == "unchanged"
    assert second["registry_metadata_changed"] is True
    assert second["current_registry_sha256"] != first["manifest"]["registry_sha256"]
    assert second["manifest"] == first["manifest"]
    assert second["manifest"]["license"] == "CC0-1.0"
