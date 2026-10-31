"""Prepare an immutable, private human-labeling packet; no linkage model or merges."""

from __future__ import annotations

import csv
import io
import json
import math
import re
import tempfile
from collections import Counter
from pathlib import Path

import pyarrow.parquet as parquet
import yaml

from pipeline.link.candidates import prepare_review
from pipeline.link.review_page import render_review
from pipeline.link.scope import review_eligibility
from pipeline.normalize.core import accept_outputs, sha256
from pipeline.pilot.core import check as check_pilot
from pipeline.scaffold import digest, json_bytes

DIRECTORY = "data/review/linkage"
RECEIPT = "reports/linkage-review-audit.json"
SAMPLE_SIZE = 200
SEED = "atlas-gold-v1"
LABEL_FIELDS = (
    "pair_id",
    "left_record_id",
    "right_record_id",
    "label",
    "reviewer",
    "notes",
    "sample_sha256",
)
ARTIFACTS = frozenset(
    f"{DIRECTORY}/{name}"
    for name in ("candidates.json", "sample.json", "labels-blank.csv", "index.html", "README.txt")
)
DETAIL_FIELDS = (
    "record_id",
    "source_id",
    "native_id",
    "country",
    "location_precision",
    "lat",
    "lon",
    "record_kind",
    "source_url",
    "source_file",
    "source_table",
    "source_row",
    "retrieved_at",
    "registered_at",
    "raw_sha256",
    "source_currency",
    "evidence_grade",
)
JSON_FIELDS = (
    "aliases",
    "country_iso3s",
    "pilot_families",
    "pilot_commodities",
    "pilot_evidence",
    "raw_commodities",
    "raw_status",
    "raw_deposit_type",
    "reference",
)


class ReviewError(ValueError):
    """Review preparation or its retained receipt is inconsistent."""


def implementation_hashes(root: Path) -> dict:
    paths = [*sorted((root / "pipeline/link").glob("*.py")), root / "uv.lock"]
    return {path.relative_to(root).as_posix(): sha256(path) for path in paths}


def record_detail(row: dict, scope: dict) -> dict:
    detail = {field: row[field] for field in DETAIL_FIELDS}
    detail.update({field: json.loads(row[field]) for field in JSON_FIELDS})
    detail.update(name=row["raw_name"], precision_policy=scope["precision_policy"])
    # ICMM documents semicolon-separated Group Names. Keep the original cell in
    # aliases_raw and split only this documented source convention for comparisons.
    detail["aliases_raw"] = list(detail["aliases"])
    if row["source_id"] == "icmm":
        detail["aliases"] = sorted(
            {part.strip() for cell in detail["aliases"] for part in cell.split(";") if part.strip()}
        )
    return detail


def load_assets(root: Path, parent: dict) -> tuple[list[dict], dict, list[dict]]:
    assets, details, sources = [], {}, []
    for source in parent["sources"]:
        reasons, precisions, accepted, count = Counter(), Counter(), 0, 0
        for batch in parquet.ParquetFile(root / source["artifact"]).iter_batches(batch_size=10000):
            for row in batch.to_pylist():
                count += 1
                if row["source_id"] != source["source_id"]:
                    raise ReviewError("Pilot file contains another source")
                scope = review_eligibility(row)
                if not scope["eligible"]:
                    reasons[scope["reason"]] += 1
                    continue
                detail = record_detail(row, scope)
                record_id = row["record_id"]
                if record_id in details:
                    raise ReviewError("Duplicate eligible record ID")
                details[record_id] = detail
                assets.append(
                    {
                        "record_id": record_id,
                        "source_id": row["source_id"],
                        "raw_name": row["raw_name"],
                        "aliases": detail["aliases"],
                        "lat": row["lat"],
                        "lon": row["lon"],
                        "country_iso3s": detail["country_iso3s"],
                        "pilot_families": detail["pilot_families"],
                        "record_kind": row["record_kind"],
                        "radius_km": scope["radius_km"],
                        "precision_policy": scope["precision_policy"],
                    }
                )
                accepted += 1
                precisions[row["location_precision"] or "unknown"] += 1
        if count != source["kept_rows"]:
            raise ReviewError("Pilot input count changed")
        sources.append(
            {
                "source_id": source["source_id"],
                "input_rows": count,
                "eligible_rows": accepted,
                "excluded_rows": count - accepted,
                "exclusion_reasons": dict(sorted(reasons.items())),
                "eligible_precision": dict(sorted(precisions.items())),
                "input_artifact": source["artifact"],
                "input_sha256": source["sha256"],
            }
        )
    return assets, details, sources


def label_csv(pairs: list[dict], sample_sha256: str) -> bytes:
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=LABEL_FIELDS, lineterminator="\n")
    writer.writeheader()
    for pair in pairs:
        writer.writerow(
            {
                **{key: pair[key] for key in LABEL_FIELDS[:3]},
                "label": "",
                "reviewer": "",
                "notes": "",
                "sample_sha256": sample_sha256,
            }
        )
    return stream.getvalue().encode()


def sample_digest(sample: dict) -> str:
    return digest(
        json_bytes({key: value for key, value in sample.items() if key != "sample_sha256"})
    )


def packet(assets: list[dict], details: dict, source_names: dict) -> tuple[dict, dict, dict]:
    prepared = prepare_review(assets, size=SAMPLE_SIZE, seed=SEED)
    pairs = [
        {**pair, "left": details[pair["left_record_id"]], "right": details[pair["right_record_id"]]}
        for pair in prepared["pairs"]
    ]
    sample = {
        "schema_version": "atlas-gold-review-v1",
        "pairs": pairs,
        "metadata": prepared["metadata"],
        "source_names": source_names,
    }
    sample["sample_sha256"] = sample_digest(sample)
    metadata = {"sample_sha256": sample["sample_sha256"], "source_names": source_names}
    readme = (
        "Critical Minerals Atlas — local site-identity review\n\n"
        "Open index.html in a browser. Compare each of the 200 pairs and choose Same site, "
        "Different sites, or Uncertain. All decisions start blank. Source links, original "
        "values and provenance are included. Add your reviewer name and export decisions "
        "as a new CSV. Export partial progress regularly; browser storage is not a backup.\n\n"
        "Uncertain and unreviewed pairs must be resolved before model tuning. This packet "
        "contains no model predictions, trained scores, merges or canonical site IDs. "
        "Proximity alone does not establish identity. Unknown coordinate precision is "
        "preserved; a conservative 10 km radius is used for review candidates only.\n\n"
        "The sample is stratified by source pair, shared commodity family, distance and "
        "name category. Some joint strata may be absent from a 200-pair sample; the "
        "metadata records coverage and weights. These weights do not establish population "
        "precision or recall. The sampled candidates cannot measure matches missed by "
        "blocking; that limitation must be reported in later evaluation.\n\n"
        "These files include source-level data subject to source-specific reuse terms. "
        "Keep the packet local. It is excluded from Git and public site outputs. "
        "Do not edit sample.json, candidates.json, labels-blank.csv or index.html. "
        "Exported decisions are separate files; validation never modifies source data.\n\n"
        "Validate from the repository: uv run --locked python -m pipeline.link "
        "--labels /absolute/path/to/export.csv\n"
        "Add --partial to validate an unfinished review.\n\n"
        f"Sample SHA-256: {sample['sample_sha256']}\n"
    ).encode()
    outputs = {
        f"{DIRECTORY}/candidates.json": json_bytes(prepared["candidates"]),
        f"{DIRECTORY}/sample.json": json_bytes(sample),
        f"{DIRECTORY}/labels-blank.csv": label_csv(pairs, sample["sample_sha256"]),
        f"{DIRECTORY}/index.html": render_review(pairs, metadata),
        f"{DIRECTORY}/README.txt": readme,
    }
    return outputs, sample, prepared["metadata"]


def source_names(root: Path, sources: list[dict]) -> dict:
    names = {}
    for source in sources:
        source_id = source["source_id"]
        path = root / "sources" / f"{source_id}.yaml"
        registry = yaml.safe_load(path.read_text())
        names[source_id] = registry.get("name", source_id)
    return names


def prepare(root: Path) -> dict:
    root = root.resolve()
    parent = check_pilot(root, raw=True)
    assets, details, sources = load_assets(root, parent)
    outputs, sample, sampling = packet(assets, details, source_names(root, sources))
    receipt = {
        "schema_version": "linkage-review-audit-v1",
        "phase": 0,
        "step": 6,
        "stage": "gold_review_preparation",
        "status": "awaiting_human_labels",
        "model_fitted": False,
        "auto_merges": 0,
        "canonical_rows": 0,
        "published_rows": 0,
        "human_labels": 0,
        "input_rows": parent["kept_rows"],
        "eligible_rows": len(assets),
        "excluded_rows": parent["kept_rows"] - len(assets),
        "candidate_pairs": sampling["candidate_count"],
        "sample_size": SAMPLE_SIZE,
        "sample_sha256": sample["sample_sha256"],
        "sampling": sampling,
        "sources": sources,
        "pilot_audit_sha256": sha256(root / "reports/pilot-audit.json"),
        "implementation": implementation_hashes(root),
        "artifacts": {
            path: {"sha256": digest(data), "bytes": len(data)}
            for path, data in sorted(outputs.items())
        },
        "privacy": "Detailed source/pair artifacts stay local, Git-ignored and unpublished.",
        "scope": "Deposit and explicit mine points only; exclusions preserve original pilot rows.",
        "precision_policy": "Unknown precision stays unknown; 10 km review candidates only.",
        "evaluation_limit": (
            "No accuracy claim before human labels and held-out evaluation. Unrepresented "
            "joint strata and matches outside blocking are not evaluated by this sample."
        ),
    }
    validate_receipt(receipt, parent)
    outputs[RECEIPT] = json_bytes(receipt)
    with tempfile.TemporaryDirectory(prefix=".atlas-review-", dir=root / "data") as temporary:
        temp = Path(temporary)
        staged = []
        for relative, content in outputs.items():
            path = temp / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
            staged.append((path, root / relative))
        accept_outputs(staged)  # Receipt is last; unchanged files retain their mtimes.
    return receipt


def validate_receipt(receipt: dict, parent: dict) -> None:
    if (
        (
            receipt["schema_version"],
            receipt["phase"],
            receipt["step"],
            receipt["stage"],
            receipt["status"],
        )
        != ("linkage-review-audit-v1", 0, 6, "gold_review_preparation", "awaiting_human_labels")
        or receipt["model_fitted"] is not False
        or any(
            receipt[key] != 0
            for key in ("auto_merges", "canonical_rows", "published_rows", "human_labels")
        )
        or receipt["sample_size"] != SAMPLE_SIZE
        or receipt["input_rows"] != parent["kept_rows"]
        or receipt["eligible_rows"] + receipt["excluded_rows"] != receipt["input_rows"]
    ):
        raise ReviewError("Invalid review checkpoint status or totals")
    parents = {source["source_id"]: source for source in parent["sources"]}
    sources = receipt["sources"]
    if len(sources) != len(parents) or {s["source_id"] for s in sources} != set(parents):
        raise ReviewError("Missing or repeated source reconciliation")
    for source in sources:
        before = parents[source["source_id"]]
        if (
            source["input_rows"] != before["kept_rows"]
            or source["input_sha256"] != before["sha256"]
            or source["input_artifact"] != before["artifact"]
            or source["eligible_rows"] + source["excluded_rows"] != source["input_rows"]
            or sum(source["exclusion_reasons"].values()) != source["excluded_rows"]
            or sum(source["eligible_precision"].values()) != source["eligible_rows"]
            or any(
                type(n) is not int or n < 0
                for n in (
                    source["input_rows"],
                    source["eligible_rows"],
                    source["excluded_rows"],
                    *source["exclusion_reasons"].values(),
                    *source["eligible_precision"].values(),
                )
            )
        ):
            raise ReviewError("Review source totals do not reconcile")
    if any(
        sum(s[key] for s in sources) != receipt[key]
        for key in ("input_rows", "eligible_rows", "excluded_rows")
    ):
        raise ReviewError("Review grand totals do not reconcile")
    sampling = receipt["sampling"]
    if (
        sampling["sample_size"] != SAMPLE_SIZE
        or sampling["seed"] != SEED
        or sampling["candidate_count"] != receipt["candidate_pairs"]
        or sampling["eligible_asset_count"] != receipt["eligible_rows"]
        or sampling["label_status"] != "blank_awaiting_human_review"
        or sum(s["candidate_count"] for s in sampling["strata"]) != receipt["candidate_pairs"]
        or sum(s["sample_quota"] for s in sampling["strata"]) != SAMPLE_SIZE
        or len({s["stratum_id"] for s in sampling["strata"]}) != len(sampling["strata"])
    ):
        raise ReviewError("Sampling totals do not reconcile")
    available, covered = set(), set()
    for stratum in sampling["strata"]:
        count, quota, weight = (
            stratum[k] for k in ("candidate_count", "sample_quota", "sample_weight")
        )
        if type(count) is not int or type(quota) is not int or not 0 <= quota <= count or count < 1:
            raise ReviewError("Invalid sampling quota")
        if (not quota and weight is not None) or (
            quota
            and (not isinstance(weight, (int, float)) or not math.isclose(weight, count / quota))
        ):
            raise ReviewError("Incorrect sampling weight")
        pair, families = stratum["source_pair"], stratum["shared_families"]
        distance, name = stratum["distance_band"], stratum["name_category"]
        if (
            len(pair) != 2
            or pair != sorted(pair)
            or set(pair) - parents.keys()
            or not families
            or families != sorted(set(families))
            or set(families) - {"REE", "Ni", "Co"}
            or distance
            not in {"0_to_1_km", "over_1_to_5_km", "over_5_to_10_km", "over_10_to_25_km"}
            or name
            not in {
                "exact_normalized",
                "multiple_shared_tokens",
                "one_shared_token",
                "no_shared_tokens",
                "missing_name",
            }
        ):
            raise ReviewError("Invalid sampling stratum descriptors")
        identity = [pair, families, distance, name]
        expected_id = "stratum_" + digest(
            json.dumps(identity, ensure_ascii=False, separators=(",", ":")).encode()
        )
        if stratum["stratum_id"] != expected_id:
            raise ReviewError("Sampling stratum ID does not match its descriptors")
        margins = {
            *(("source", source) for source in pair),
            *(("family", family) for family in families),
            ("source_pair", " | ".join(pair)),
            ("distance_band", distance),
            ("name_category", name),
        }
        available.update(margins)
        if quota:
            covered.update(margins)
    coverage = {}
    for dimension in ("source", "family", "source_pair", "distance_band", "name_category"):
        values = sorted(value for d, value in available if d == dimension)
        sampled = sorted(value for d, value in covered if d == dimension)
        coverage[dimension] = {
            "available": values,
            "sampled": sampled,
            "unrepresented": sorted(set(values) - set(sampled)),
        }
    if sampling["coverage"] != coverage:
        raise ReviewError("Sampling coverage does not reconcile with stratum quotas")
    if set(receipt["artifacts"]) != ARTIFACTS:
        raise ReviewError("Unexpected review artifact paths")
    for item in receipt["artifacts"].values():
        if (
            not re.fullmatch(r"[0-9a-f]{64}", str(item["sha256"]))
            or type(item["bytes"]) is not int
            or item["bytes"] < 1
        ):
            raise ReviewError("Invalid review artifact receipt")
    if not re.fullmatch(r"[0-9a-f]{64}", str(receipt["sample_sha256"])):
        raise ReviewError("Invalid sample SHA-256")


def check(root: Path, *, raw=False) -> dict:
    parent = check_pilot(root, raw=raw)
    receipt = json.loads((root / RECEIPT).read_text())
    validate_receipt(receipt, parent)
    if receipt["implementation"] != implementation_hashes(root):
        raise ReviewError("Review implementation changed; rebuild its packet")
    if receipt["pilot_audit_sha256"] != sha256(root / "reports/pilot-audit.json"):
        raise ReviewError("Pilot receipt changed since review preparation")
    if raw:
        # Rebuild the complete sampling design from verified pilot files, then
        # compare every private output. The HTML/CSV must still start unlabelled.
        assets, details, sources = load_assets(root, parent)
        outputs, sample, sampling = packet(assets, details, source_names(root, sources))
        if sources != receipt["sources"] or sampling != receipt["sampling"]:
            raise ReviewError("Review source eligibility or sampling changed")
        if sample["sample_sha256"] != receipt["sample_sha256"]:
            raise ReviewError("Selected review sample changed")
        for path, data in outputs.items():
            if receipt["artifacts"][path] != {"sha256": digest(data), "bytes": len(data)}:
                raise ReviewError("Generated review artifact differs from its receipt")
            if (root / path).read_bytes() != data:
                raise ReviewError("Local review artifact changed")
    return receipt
