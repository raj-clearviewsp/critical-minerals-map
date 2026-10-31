"""Validate review-page CSV exports against an accepted, unchanged gold sample.

This checks identities, sample binding and review completeness only. It does not
change labels, establish their accuracy, fit a model, or merge source records.
The caller verifies the accepted sample artifact's digest before supplying it.
"""

from __future__ import annotations

import csv
import re
from pathlib import Path

HEADERS = (
    "pair_id",
    "left_record_id",
    "right_record_id",
    "label",
    "reviewer",
    "notes",
    "sample_sha256",
)
LABELS = frozenset({"", "same_site", "different_sites", "uncertain"})
GOLD_SIZE = 200
SHA256 = re.compile(r"[0-9a-f]{64}\Z")


class LabelValidationError(ValueError):
    """An export is stale, malformed, altered, or not yet decisively reviewed."""


def _sample_index(sample: dict) -> tuple[str, dict[str, dict]]:
    if not isinstance(sample, dict):
        raise LabelValidationError("Accepted sample must be an object")
    sha = sample.get("sample_sha256")
    if not isinstance(sha, str) or not SHA256.fullmatch(sha):
        raise LabelValidationError("Accepted sample requires a lowercase hexadecimal SHA-256")
    pairs = sample.get("pairs")
    if not isinstance(pairs, list) or len(pairs) != GOLD_SIZE:
        raise LabelValidationError(f"Accepted sample must contain exactly {GOLD_SIZE} pairs")
    index, identities = {}, set()
    for pair in pairs:
        if not isinstance(pair, dict) or any(
            not isinstance(pair.get(field), str) or not pair[field].strip() for field in HEADERS[:3]
        ):
            raise LabelValidationError("Accepted sample requires nonblank string pair/record IDs")
        identity = tuple(sorted((pair["left_record_id"], pair["right_record_id"])))
        if pair["pair_id"] in index or identity in identities or identity[0] == identity[1]:
            raise LabelValidationError(
                "Accepted sample contains duplicate IDs, duplicate pairs or self-pairs"
            )
        index[pair["pair_id"]] = pair
        identities.add(identity)
    return sha, index


def validate_labels(csv_path: Path, sample: dict, require_complete: bool = True) -> dict:
    """Validate all 200 sample rows, optionally allowing in-progress human labels.

    Every export, including partial review, contains the complete accepted pair
    list with unchanged ordered record IDs and sample SHA-256. Blank labels and
    ``uncertain`` remain unresolved; only same_site/different_sites are decisive.
    Nonblank labels require a named reviewer. Returned rows follow sample order
    and preserve the exact supplied reviewer/notes text. No input is modified.
    """
    if not isinstance(require_complete, bool):
        raise LabelValidationError("require_complete must be a boolean")
    sample_sha, expected = _sample_index(sample)
    actual = {}
    try:
        # UTF-8 BOM is common in CSV exports opened in spreadsheet software.
        with Path(csv_path).open(encoding="utf-8-sig", newline="") as stream:
            reader = csv.reader(stream, strict=True)
            if next(reader, None) != list(HEADERS):
                raise LabelValidationError(
                    "Review CSV headers must exactly match: " + ",".join(HEADERS)
                )
            for cells in reader:
                if len(cells) != len(HEADERS):
                    raise LabelValidationError(
                        f"Review CSV line {reader.line_num} has {len(cells)} fields; "
                        f"expected {len(HEADERS)}"
                    )
                row = dict(zip(HEADERS, cells, strict=True))
                pair_id = row["pair_id"]
                if pair_id in actual:
                    raise LabelValidationError(f"Duplicate review pair_id: {pair_id}")
                if pair_id not in expected:
                    raise LabelValidationError(f"Unexpected review pair_id: {pair_id}")
                original = expected[pair_id]
                if any(row[field] != original[field] for field in HEADERS[1:3]):
                    raise LabelValidationError(f"Changed record IDs for review pair: {pair_id}")
                if row["sample_sha256"] != sample_sha:
                    raise LabelValidationError(
                        f"Stale or changed sample_sha256 for pair: {pair_id}"
                    )
                if row["label"] not in LABELS:
                    raise LabelValidationError(
                        f"Unsupported label for pair {pair_id}: {row['label']!r}"
                    )
                if row["label"] and not row["reviewer"].strip():
                    raise LabelValidationError(
                        f"A reviewer is required for labelled pair: {pair_id}"
                    )
                actual[pair_id] = row
    except (csv.Error, UnicodeError) as error:
        raise LabelValidationError(f"Review export is not a valid UTF-8 CSV: {error}") from error
    if missing := set(expected) - set(actual):
        raise LabelValidationError(
            f"Review CSV is missing {len(missing)} accepted sample rows; "
            f"exactly {GOLD_SIZE} are required"
        )
    rows = [actual[pair_id] for pair_id in expected]
    counts = {"blank": 0, "same_site": 0, "different_sites": 0, "uncertain": 0}
    for row in rows:
        counts[row["label"] or "blank"] += 1
    unresolved = [r["pair_id"] for r in rows if r["label"] in {"", "uncertain"}]
    complete = not unresolved
    if require_complete and not complete:
        raise LabelValidationError(
            f"Human review incomplete: {counts['blank']} blank and {counts['uncertain']} uncertain "
            f"pairs remain unresolved; all {GOLD_SIZE} require same_site or different_sites"
        )
    return {
        "sample_sha256": sample_sha,
        "row_count": len(rows),
        "counts": counts,
        "decisive_count": counts["same_site"] + counts["different_sites"],
        "unresolved_count": len(unresolved),
        "unresolved_pair_ids": unresolved,
        "complete": complete,
        "rows": rows,
    }
