"""Register original local downloads, without contacting a remote service."""

from __future__ import annotations

import hashlib
import json
import shutil
import tempfile
import zipfile
import zlib
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path

import jsonschema
import yaml

from pipeline.fetch.core import (
    FILENAME,
    MAX_BYTES,
    SCHEMAS,
    FetchError,
    acquisition_approval,
    load_source,
    validate_license,
    validate_url,
    verify_snapshot,
)
from pipeline.scaffold import digest, json_bytes

MAX_EXPANDED_BYTES = 512 * 1024 * 1024


def load_intake(path: Path) -> dict:
    """JSON is a YAML subset; resolve relative input paths against the intake file."""
    try:
        value = yaml.safe_load(path.read_text())
        jsonschema.validate(
            value,
            json.loads((SCHEMAS / "local-intake.schema.json").read_text()),
            format_checker=jsonschema.FormatChecker(),
        )
        for item in value["files"]:
            local = Path(item["local_path"]).expanduser()
            item["local_path"] = str(local if local.is_absolute() else path.parent / local)
        return value
    except (OSError, yaml.YAMLError, jsonschema.ValidationError) as error:
        raise FetchError("Invalid local intake; check local-intake.schema.json") from error


def _publisher_constraints(source: dict) -> dict[str, dict]:
    """Merge declarations without letting a weaker duplicate discard verification."""
    constraints: dict[str, dict] = {}
    for item in source["fetcher"].get("files", []) + source.get("candidate_files", []):
        name = item["filename"]
        expected = constraints.setdefault(name, {"checksums": {}})
        for key in ("publisher_bytes", "bytes"):
            size = item.get(key)
            if size is not None:
                if "bytes" in expected and expected["bytes"] != size:
                    raise FetchError(f"Conflicting publisher byte counts: {name}")
                expected["bytes"] = size
        checksum = item.get("publisher_checksum")
        if checksum:
            if isinstance(checksum, dict):
                algorithm, value = checksum["type"], checksum["value"]
            else:
                algorithm, value = checksum.split(":", 1)
            algorithm, value = algorithm.lower().replace("-", ""), value.lower()
            if algorithm not in ("md5", "sha256"):
                raise FetchError("Unsupported publisher checksum algorithm")
            previous = expected["checksums"].get(algorithm)
            if previous is not None and previous != value:
                raise FetchError(f"Conflicting publisher checksums: {name}")
            expected["checksums"][algorithm] = value
    return constraints


def _check_file_format(path: Path) -> None:
    """Reject failed browser downloads while leaving record parsing to Step 4."""
    with path.open("rb") as stream:
        prefix = stream.read(4096)
    if not prefix:
        raise FetchError(f"Empty local download: {path.name}")
    text_start = prefix.decode("utf-8-sig", errors="replace").lstrip().lower()
    extension = path.suffix.lower()
    if extension in {".zip", ".xlsx", ".xls", ".csv", ".pdf"} and text_start.startswith(
        ("<!doctype html", "<html")
    ):
        raise FetchError(f"HTML error page is not a dataset: {path.name}")
    if extension in {".zip", ".xlsx"}:
        try:
            with zipfile.ZipFile(path) as archive:
                if sum(item.file_size for item in archive.infolist()) > MAX_EXPANDED_BYTES:
                    raise FetchError(f"Archive expands beyond {MAX_EXPANDED_BYTES} bytes")
                names = set(archive.namelist())
                if (
                    extension == ".xlsx"
                    and not {
                        "[Content_Types].xml",
                        "xl/workbook.xml",
                    }
                    <= names
                ):
                    raise FetchError(f"Not an Excel workbook: {path.name}")
                # Read in bounded chunks to exercise decompression and each member's
                # CRC. Directory inspection alone accepts corrupt browser downloads.
                expanded = 0
                for item in archive.infolist():
                    if item.flag_bits & 1:
                        raise FetchError(f"Encrypted archive member is not supported: {path.name}")
                    with archive.open(item) as member:
                        while chunk := member.read(1024 * 1024):
                            expanded += len(chunk)
                            if expanded > MAX_EXPANDED_BYTES:
                                raise FetchError(
                                    f"Archive expands beyond {MAX_EXPANDED_BYTES} bytes"
                                )
        except (zipfile.BadZipFile, zlib.error, EOFError, NotImplementedError) as error:
            raise FetchError(f"Invalid or incomplete ZIP download: {path.name}") from error
    if extension == ".pdf" and not prefix.startswith(b"%PDF-"):
        raise FetchError(f"Invalid PDF download: {path.name}")


def _capture(item: dict, target: Path, expected: dict) -> dict:
    name = item["filename"]
    if not FILENAME.fullmatch(name) or name.casefold() == "manifest.json":
        raise FetchError("Invalid or reserved local filename")
    validate_url(item["url"])
    if item.get("final_url") is not None:
        validate_url(item["final_url"])
    source = Path(item["local_path"])
    if source.is_symlink() or not source.is_file():
        raise FetchError(f"Local input must be a regular file: {name}")
    before = source.stat()
    if before.st_size > MAX_BYTES:
        raise FetchError(f"Local download exceeds {MAX_BYTES} bytes: {name}")
    hashes = {algorithm: hashlib.new(algorithm) for algorithm in ("sha256", "md5")}
    size = 0
    with source.open("rb") as original, target.open("xb") as copied:
        while chunk := original.read(1024 * 1024):
            size += len(chunk)
            if size > MAX_BYTES:
                raise FetchError(f"Local download exceeds {MAX_BYTES} bytes: {name}")
            copied.write(chunk)
            for value in hashes.values():
                value.update(chunk)
    after = source.stat()
    if (before.st_size, before.st_mtime_ns, before.st_ino) != (
        after.st_size,
        after.st_mtime_ns,
        after.st_ino,
    ):
        raise FetchError(f"Local input changed during import: {name}")
    expected_size = expected.get("bytes")
    if expected_size is not None and size != expected_size:
        raise FetchError(f"Publisher byte count mismatch: {name}")
    for algorithm, value in expected.get("checksums", {}).items():
        if hashes[algorithm].hexdigest() != value.lower():
            raise FetchError(f"Publisher checksum mismatch: {name}")
    _check_file_format(target)
    return {
        "path": name,
        "url": item["url"],
        "final_url": item.get("final_url"),
        "bytes": size,
        "sha256": hashes["sha256"].hexdigest(),
        "retrieved_at": item["retrieved_at"],
        "retrieval_evidence": item.get("retrieval_evidence"),
    }


def import_local(root: Path, intake: dict, *, now: datetime | None = None) -> dict:
    """Copy a reviewed intake atomically, preserving original bytes and unknown dates.

    Browser identity, access permission and provenance must have been checked during
    acquisition. This importer makes no HTTP requests and confers no reuse permission.
    """
    try:
        jsonschema.validate(
            intake,
            json.loads((SCHEMAS / "local-intake.schema.json").read_text()),
            format_checker=jsonschema.FormatChecker(),
        )
    except jsonschema.ValidationError as error:
        raise FetchError("Invalid local acquisition intake") from error
    source_id = intake["source_id"]
    source = load_source(root, source_id)
    registry_hash = digest(json_bytes(source))
    approval = acquisition_approval(root, source_id)
    validate_license(source, acquisition_approved=approval is not None)
    if source["access_status"] not in ("automated", "manual_required", "manual_only"):
        raise FetchError(f"{source_id}: local acquisition is excluded or unreviewed")
    now = now or datetime.now(UTC)
    if now.tzinfo is None:
        raise FetchError("Registration timestamp must be timezone-aware")
    now = now.astimezone(UTC)
    files = intake["files"]
    names = [item["filename"] for item in files]
    if len(names) != len({name.casefold() for name in names}):
        raise FetchError("Duplicate local filenames")
    expected = _publisher_constraints(source)
    if missing := set(expected) - set(names):
        raise FetchError(f"Intake is missing registered files: {', '.join(sorted(missing))}")
    for item in files:
        retrieved = item["retrieved_at"]
        if retrieved is not None:
            try:
                moment = datetime.fromisoformat(retrieved)
            except ValueError as error:
                raise FetchError("Invalid retrieval timestamp") from error
            if moment.tzinfo is None:
                raise FetchError("Retrieval timestamp must be timezone-aware")
            if moment > now:
                raise FetchError("Retrieval timestamp cannot follow registration")
            if not item.get("retrieval_evidence"):
                raise FetchError("Known retrieval timestamp requires its evidence")
    base = root / "data/raw" / source_id
    existing = sorted(base.glob("????-??-??"), reverse=True)
    latest = verify_snapshot(existing[0]) if existing else None
    if latest and latest["source_id"] != source_id:
        raise FetchError("Raw manifest source identity does not match the requested source")
    incoming = root / "data/.incoming"
    incoming.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=source_id + "-", dir=incoming))
    try:
        entries = [
            _capture(item, temporary / item["filename"], expected.get(item["filename"], {}))
            for item in sorted(files, key=lambda item: item["filename"])
        ]
        if latest and sorted((row["path"], row["sha256"]) for row in latest["files"]) == [
            (row["path"], row["sha256"]) for row in entries
        ]:
            return {
                "status": "unchanged",
                "snapshot": str(existing[0]),
                "manifest": latest,
                "registry_metadata_changed": latest["registry_sha256"] != registry_hash,
                "current_registry_sha256": registry_hash,
            }
        target = base / now.date().isoformat()
        if target.exists():
            raise FetchError(
                "Source changed within an existing daily snapshot; preserve it for review"
            )
        retrievals = [row["retrieved_at"] for row in entries]
        retrieved_at = None
        if all(retrievals):
            retrieved_at = max(datetime.fromisoformat(value) for value in retrievals).isoformat()
        manifest = {
            "schema_version": "raw-v2",
            "source_id": source_id,
            "retrieved_at": retrieved_at,
            "registered_at": now.isoformat(),
            "acquisition_method": intake["acquisition_method"],
            "registry_sha256": registry_hash,
            "source_url": source["url"],
            "license": source["license"],
            "license_evidence_url": source["license_evidence_url"],
            "redistribution_ok": source["redistribution_ok"],
            "acquisition_approval": approval,
            "files": entries,
        }
        (temporary / "manifest.json").write_bytes(json_bytes(manifest))
        verify_snapshot(temporary)
        base.mkdir(parents=True, exist_ok=True)
        if target.exists():
            raise FetchError("Snapshot already created by another run")
        temporary.rename(target)
        return {"status": "imported", "snapshot": str(target), "manifest": manifest}
    except OSError as error:
        raise FetchError("Local file could not be copied; no snapshot was accepted") from error
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)
        with suppress(OSError):
            incoming.rmdir()
