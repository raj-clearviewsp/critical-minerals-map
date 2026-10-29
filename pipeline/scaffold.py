"""Deterministic empty-input pipeline; real inputs intentionally fail closed."""

from __future__ import annotations

import hashlib
import io
import json
import os
import tempfile
from datetime import datetime
from pathlib import Path

import jsonschema
import pandas as pd

from pipeline.qa.schemas import TABLE_FIELDS, empty_table, validate_empty_table

STAGES = ("fetch", "normalize", "harmonize", "link", "enrich", "qa", "publish")
CONTRACT = Path(__file__).resolve().parent.parent / "schemas/manifest.schema.json"


class ScaffoldError(RuntimeError):
    """An input needs a later step or conflicts with a preserved artifact."""


def json_bytes(value: object) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True) + "\n").encode()


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def parquet_bytes(name: str) -> bytes:
    stream = io.BytesIO()
    empty_table(name).to_parquet(stream, engine="pyarrow", index=False)
    return stream.getvalue()


def artifact_files(directory: Path) -> list[Path]:
    if not directory.exists():
        return []
    return sorted(
        path for path in directory.rglob("*") if path.is_file() and path.name != ".gitkeep"
    )


def write_preserving(root: Path, outputs: dict[str, bytes]) -> None:
    """Preflight all writes, then atomically create absent files; never replace content."""
    for relative, content in outputs.items():
        target = root / relative
        if target.exists() and target.read_bytes() != content:
            raise ScaffoldError(f"Refusing to overwrite an existing artifact: {relative}")
    for relative, content in outputs.items():
        target = root / relative
        if target.exists():
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(prefix=".atlas-", dir=target.parent)
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(content)
            # hard-link creation is atomic and never clobbers a concurrently created file.
            try:
                os.link(temporary, target)
            except FileExistsError:
                if target.read_bytes() != content:
                    raise ScaffoldError(f"Concurrent artifact conflict: {relative}") from None
        finally:
            Path(temporary).unlink(missing_ok=True)


def validate_release_tag(tag: str) -> None:
    try:
        parsed = datetime.strptime(tag, "%Y.%m.%d")
    except ValueError as error:
        raise ScaffoldError("Release tag must be a valid calendar date YYYY.MM.DD") from error
    if parsed.strftime("%Y.%m.%d") != tag:
        raise ScaffoldError("Release tag must use zero-padded YYYY.MM.DD")


def ensure_empty_inputs(root: Path) -> None:
    if artifact_files(root / "data/raw"):
        raise ScaffoldError(
            "Raw inputs require the source fetch/normalization steps; scaffold stopped"
        )
    registered = list((root / "sources").glob("*.yaml")) + list((root / "sources").glob("*.yml"))
    if registered:
        raise ScaffoldError("Registered sources require Step 2 fetchers; scaffold stopped")
    for area in ("staging", "canonical"):
        for path in artifact_files(root / f"data/{area}"):
            allowed = {"source_record"} if area == "staging" else TABLE_FIELDS
            if path.suffix != ".parquet" or path.stem not in allowed:
                raise ScaffoldError(f"Unexpected {area} input: {path.relative_to(root)}")
            try:
                validate_empty_table(path.stem, pd.read_parquet(path))
            except Exception as error:
                raise ScaffoldError(
                    f"Invalid or nonempty table: {path.relative_to(root)}"
                ) from error
    allowed_outputs = {"manifest.json"} | {
        f"{name}.{extension}" for name in TABLE_FIELDS for extension in ("csv", "parquet")
    }
    for path in artifact_files(root / "data/publish"):
        if path.parent != root / "data/publish" or path.name not in allowed_outputs:
            raise ScaffoldError(f"Unexpected publish artifact: {path.relative_to(root)}")
        if path.suffix == ".parquet":
            try:
                validate_empty_table(path.stem, pd.read_parquet(path))
            except Exception as error:
                raise ScaffoldError(f"Invalid or nonempty published table: {path.name}") from error
        elif path.suffix == ".csv":
            expected = empty_table(path.stem).to_csv(index=False).encode()
            if path.read_bytes() != expected:
                raise ScaffoldError(f"Invalid or nonempty published CSV: {path.name}")
        else:
            validate_manifest(path)


def validate_manifest(path: Path) -> dict:
    try:
        value = json.loads(path.read_text())
        jsonschema.validate(value, json.loads(CONTRACT.read_text()))
        validate_release_tag(value["release_tag"])
        for name, metadata in value["tables"].items():
            for extension in ("csv", "parquet"):
                if metadata[extension] != f"{name}.{extension}":
                    raise ScaffoldError(f"Manifest path does not match table: {name}")
                payload = (path.parent / metadata[extension]).read_bytes()
                if digest(payload) != metadata[f"{extension}_sha256"]:
                    raise ScaffoldError(f"Manifest checksum mismatch: {metadata[extension]}")
        return value
    except ScaffoldError:
        raise
    except Exception as error:
        raise ScaffoldError(f"Invalid or incomplete manifest: {path}") from error


def prior_receipt(root: Path, stage: str, release_tag: str) -> None:
    position = STAGES.index(stage)
    if not position:
        return
    previous = STAGES[position - 1]
    try:
        receipt = json.loads((root / f"reports/runs/{previous}.json").read_text())
        if receipt["stage"] != previous or receipt["release_tag"] != release_tag:
            raise ValueError("Stage or release mismatch")
        if receipt["status"] != "scaffold_empty_pass" or receipt["rows"] != 0:
            raise ValueError("Invalid receipt")
        expected = set()
        if previous == "normalize":
            expected = {"data/staging/source_record.parquet"}
        elif previous in ("harmonize", "link", "enrich", "qa"):
            expected = {f"data/canonical/{name}.parquet" for name in TABLE_FIELDS}
        if set(receipt["artifacts"]) != expected:
            raise ValueError("Incomplete or unexpected artifact evidence")
        for name, checksum in receipt["artifacts"].items():
            if digest((root / name).read_bytes()) != checksum:
                raise ValueError(f"Changed artifact: {name}")
    except Exception as error:
        raise ScaffoldError(
            f"Missing or invalid prerequisite: run {previous} successfully first"
        ) from error


def require_canonical(root: Path) -> None:
    for name in TABLE_FIELDS:
        path = root / f"data/canonical/{name}.parquet"
        if not path.is_file():
            raise ScaffoldError(f"Missing canonical table: {name}")
        validate_empty_table(name, pd.read_parquet(path))


def run_stage(root: Path, stage: str, release_tag: str) -> dict:
    if stage not in STAGES:
        raise ScaffoldError(f"Unknown stage: {stage}")
    root = root.resolve()
    validate_release_tag(release_tag)
    ensure_empty_inputs(root)
    prior_receipt(root, stage, release_tag)
    outputs: dict[str, bytes] = {}
    if stage == "normalize":
        outputs["data/staging/source_record.parquet"] = parquet_bytes("source_record")
    elif stage == "harmonize":
        validate_empty_table(
            "source_record", pd.read_parquet(root / "data/staging/source_record.parquet")
        )
        outputs.update(
            {f"data/canonical/{name}.parquet": parquet_bytes(name) for name in TABLE_FIELDS}
        )
    elif stage in ("link", "enrich", "qa", "publish"):
        require_canonical(root)
    if stage == "publish":
        tables = {}
        for name in TABLE_FIELDS:
            csv = empty_table(name).to_csv(index=False).encode()
            parquet = (root / f"data/canonical/{name}.parquet").read_bytes()
            outputs[f"data/publish/{name}.csv"] = csv
            outputs[f"data/publish/{name}.parquet"] = parquet
            tables[name] = {
                "rows": 0,
                "csv": f"{name}.csv",
                "parquet": f"{name}.parquet",
                "csv_sha256": digest(csv),
                "parquet_sha256": digest(parquet),
            }
        manifest = {
            "schema_version": "scaffold-v1",
            "release_tag": release_tag,
            "data_as_of": None,
            "status": "scaffold",
            "phase": 0,
            "step": 1,
            "sources": [],
            "tables": tables,
            "limitations": [
                "No source records ingested. Zero rows do not mean zero mineral deposits.",
                "These are empty build artifacts, not a public data release.",
                "Nonempty inputs are disabled until the subsequent implementation steps.",
                "Spatial validation, licensing, linkage and maturity audits "
                "are not implemented yet.",
            ],
        }
        jsonschema.validate(manifest, json.loads(CONTRACT.read_text()))
        outputs["data/publish/manifest.json"] = json_bytes(manifest)
    # Receipts also carry canonical input hashes for stages that produce no data.
    artifacts = {name: digest(payload) for name, payload in outputs.items()}
    if stage in ("link", "enrich", "qa"):
        artifacts = {
            f"data/canonical/{name}.parquet": digest(
                (root / f"data/canonical/{name}.parquet").read_bytes()
            )
            for name in TABLE_FIELDS
        }
    report = {
        "stage": stage,
        "phase": 0,
        "step": 1,
        "release_tag": release_tag,
        "status": "scaffold_empty_pass",
        "rows": 0,
        "rejects": 0,
        "artifacts": artifacts,
        "note": "Empty-input scaffold only; source-specific implementation has not begun.",
    }
    outputs[f"reports/runs/{stage}.json"] = json_bytes(report)
    write_preserving(root, outputs)
    if stage == "publish":
        validate_manifest(root / "data/publish/manifest.json")
    return report
