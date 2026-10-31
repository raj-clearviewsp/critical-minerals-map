"""Preserve original decisions and validate the independent 20-case pilot export."""

from __future__ import annotations

import csv
import io
import json
import re
from pathlib import Path

from pipeline.link.labels import HEADERS, validate_labels
from pipeline.scaffold import digest, json_bytes

PILOT_SIZE = 20
LABELS = frozenset(
    {
        "",
        "same_site",
        "different_sites",
        "left_contains_right",
        "right_contains_left",
        "related_scope_unclear",
        "insufficient_evidence",
    }
)
UNRESOLVED = {"", "related_scope_unclear", "insufficient_evidence"}


class TriageLabelError(ValueError):
    """A review file changed identity, lost an original decision, or is malformed."""


def preserve_import(csv_path: Path, original_sample: dict) -> tuple[bytes, dict]:
    """Return exact bytes and all original nonblank decisions without reinterpreting them."""
    before = csv_path.read_bytes()
    result = validate_labels(csv_path, original_sample, require_complete=False)
    if csv_path.read_bytes() != before:
        raise TriageLabelError("Review export changed during validation")
    reviewed = [row for row in result["rows"] if row["label"]]
    preserved = {
        "schema_version": "preserved-human-decisions-v1",
        "original_csv_sha256": digest(before),
        "original_sample_sha256": original_sample["sample_sha256"],
        "csv_rows": result["row_count"],
        "reviewed_rows": len(reviewed),
        "counts": result["counts"],
        "decisions": reviewed,
        "policy": (
            "Exact original labels, reviewer and notes are retained. Triage is a separate "
            "machine assessment; later clarification does not overwrite this record."
        ),
    }
    return before, preserved


def sample_digest(sample: dict) -> str:
    return digest(
        json_bytes({key: value for key, value in sample.items() if key != "sample_sha256"})
    )


def blank_csv(cases: list[dict], sample_hash: str) -> bytes:
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=HEADERS, lineterminator="\n")
    writer.writeheader()
    for case in cases:
        writer.writerow(
            {
                **{field: case[field] for field in HEADERS[:3]},
                "label": "",
                "reviewer": "",
                "notes": "",
                "sample_sha256": sample_hash,
            }
        )
    return output.getvalue().encode()


def validate_pilot_labels(path: Path, sample: dict, *, require_complete=False) -> dict:
    if (
        sample.get("schema_version") != "triage-pilot-v2"
        or not re.fullmatch(r"[0-9a-f]{64}", str(sample.get("sample_sha256")))
        or sample_digest(sample) != sample["sample_sha256"]
        or len(sample.get("cases", [])) != PILOT_SIZE
    ):
        raise TriageLabelError("Invalid or changed pilot sample")
    expected = {case["pair_id"]: case for case in sample["cases"]}
    if len(expected) != PILOT_SIZE:
        raise TriageLabelError("Duplicate pilot case IDs")
    identities = set()
    for case in expected.values():
        ids = (case["left_record_id"], case["right_record_id"])
        if ids[0] == ids[1] or tuple(sorted(ids)) in identities:
            raise TriageLabelError("Duplicate or self-paired pilot records")
        identities.add(tuple(sorted(ids)))
    rows = {}
    try:
        with path.open(encoding="utf-8-sig", newline="") as stream:
            reader = csv.reader(stream, strict=True)
            if next(reader, None) != list(HEADERS):
                raise TriageLabelError("Unexpected pilot CSV columns")
            for values in reader:
                if len(values) != len(HEADERS):
                    raise TriageLabelError("Malformed pilot CSV row")
                row = dict(zip(HEADERS, values, strict=True))
                identity = row["pair_id"]
                if identity not in expected or identity in rows:
                    raise TriageLabelError("Unknown or duplicate pilot pair")
                if any(row[field] != expected[identity][field] for field in HEADERS[1:3]):
                    raise TriageLabelError("Pilot source record IDs changed")
                if row["sample_sha256"] != sample["sample_sha256"]:
                    raise TriageLabelError("Pilot CSV belongs to a different sample")
                if row["label"] not in LABELS:
                    raise TriageLabelError("Unrecognized pilot decision")
                if row["label"] and not row["reviewer"].strip():
                    raise TriageLabelError("A named reviewer is required for a decision")
                rows[identity] = row
    except (csv.Error, UnicodeError) as error:
        raise TriageLabelError("Pilot export is not a valid UTF-8 CSV") from error
    if set(rows) != set(expected):
        raise TriageLabelError("Pilot CSV must contain all 20 cases, including blank decisions")
    counts = {key or "blank": 0 for key in sorted(LABELS)}
    for row in rows.values():
        counts[row["label"] or "blank"] += 1
    reviewed = PILOT_SIZE - counts["blank"]
    unresolved = sum(row["label"] in UNRESOLVED for row in rows.values())
    if require_complete and reviewed != PILOT_SIZE:
        raise TriageLabelError(f"Pilot review has {PILOT_SIZE - reviewed} unreviewed cases")
    return {
        "sample_sha256": sample["sample_sha256"],
        "rows": [rows[key] for key in expected],
        "counts": counts,
        "reviewed": reviewed,
        "unresolved": unresolved,
        "all_cases_reviewed": reviewed == PILOT_SIZE,
        "formal_gold_set_complete": False,
    }


def load_bound_sample(root: Path, receipt: dict) -> dict:
    path = "data/review/triage-v2/pilot.json"
    content = (root / path).read_bytes()
    if digest(content) != receipt["artifacts"][path]["sha256"]:
        raise TriageLabelError("Accepted pilot artifact changed")
    sample = json.loads(content)
    if sample_digest(sample) != receipt["sample_sha256"] or (
        sample.get("sample_sha256") != receipt["sample_sha256"]
    ):
        raise TriageLabelError("Pilot sample digest differs from its receipt")
    return sample
