from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pyarrow.parquet as pq

from pipeline.normalize.core import accept_outputs, sha256
from pipeline.scaffold import digest, json_bytes

RELEASE = "data/releases/2026.11.04-v0"
IDENTITY = "reports/phase0-step06-reviewed-audit.json"
PILOT = "reports/pilot-audit.json"
STAGES = {
    "maturity": ("reports/phase0-step07-maturity-audit.json", "reports/phase0-step07-maturity.md"),
    "context": ("reports/phase0-step08-context-audit.json", "reports/phase0-step08-context.md"),
}


def load(path: Path):
    return json.loads(path.read_bytes())


def bound(root: Path, receipt: dict, name: str):
    raw = (root / name).read_bytes()
    if receipt["artifacts"][name] != {"bytes": len(raw), "sha256": digest(raw)}:
        raise ValueError(f"Changed release artifact: {name}")
    return json.loads(raw)


def pilot_rows(root: Path, source: str, columns: list[str], ids: set[str] | None = None):
    metadata = next(r for r in load(root / PILOT)["sources"] if r["source_id"] == source)
    path = root / metadata["artifact"]
    if sha256(path) != metadata["sha256"]:
        raise ValueError(f"Pilot source changed: {source}")
    table = pq.read_table(
        path, columns=columns, filters=None if ids is None else [("record_id", "in", sorted(ids))]
    )
    yield from table.to_pylist()


def provenance(row, fields, rule, reason):
    return {
        **{
            k: row.get(k)
            for k in (
                "record_id",
                "source_id",
                "source_table",
                "source_row",
                "source_file",
                "source_url",
                "raw_sha256",
                "source_currency",
                "retrieved_at",
                "evidence_grade",
                "redistribution_ok",
            )
        },
        "fields": fields,
        "rule": rule,
        "reason": reason,
    }


def checksums(root: Path, stage: str):
    directory = root / "pipeline/release_enrichment"
    paths = [directory / n for n in ("__init__.py", "__main__.py", "common.py", stage + ".py")]
    paths.extend([root / "uv.lock", directory / "mcs_chapters.json"])
    if stage == "maturity":
        paths.extend([directory / "site_statements.json", directory / "corrections.py"])
    context_sources = {"usgs_mcs_2026", "aiddata_transition_v2", "oecd_export"}
    sources = {entry["source_id"] for entry in load(root / PILOT)["sources"]}
    selected = sources - context_sources if stage == "maturity" else context_sources
    paths.extend(root / "sources" / f"{source}.yaml" for source in sorted(selected))
    return {p.relative_to(root).as_posix(): sha256(p) for p in paths}


def seal(root: Path, stage: str, files: dict[str, bytes], receipt: dict) -> dict:
    receipt["artifacts"] = {
        k: {"bytes": len(v), "sha256": digest(v)} for k, v in sorted(files.items())
    }
    receipt["implementation"] = checksums(root, stage)
    files[STAGES[stage][0]] = json_bytes(receipt)
    with tempfile.TemporaryDirectory(prefix=".enrichment-", dir=root / "data") as temp:
        staged = []
        for name, contents in files.items():
            path = Path(temp) / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(contents)
            staged.append((path, root / name))
        accept_outputs(staged)
    return receipt
