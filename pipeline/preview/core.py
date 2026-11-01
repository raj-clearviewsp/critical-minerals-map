"""Project verified observations for a local site without resolving site identities."""

from __future__ import annotations

import csv
import io
import json
import math
import re
import tempfile
from collections import Counter, defaultdict
from pathlib import Path
from urllib.parse import urlsplit

import pycountry

from pipeline.link.labels import HEADERS
from pipeline.normalize.core import accept_outputs, sha256
from pipeline.reconciliation.core import check as check_reconciliation
from pipeline.reconciliation.core import load_packet, validate_export
from pipeline.reconciliation.ledger import UNRESOLVED
from pipeline.scaffold import digest, json_bytes

RECEIPT = "reports/private-preview-audit.json"
DIRECTORY = "data/preview/site-v1"
REVIEW_DIRECTORY = "data/review/adjudication-v1"
COMPLETED = f"{REVIEW_DIRECTORY}/completed.csv"
HISTORY = f"{REVIEW_DIRECTORY}/decision-history.json"
LATEST = f"{REVIEW_DIRECTORY}/latest-decisions.json"
DATASET = f"{DIRECTORY}/dataset.json"
PACKAGE = f"{DIRECTORY}/receipt.json"
PARENT_RECEIPT = "reports/reconciliation-audit.json"
PARENT_HISTORY = "data/review/reconciliation-v1/decision-history.json"
TRIAGE_RECEIPT = "reports/triage-audit.json"
RECORDS = "data/review/triage-v2/records.json"
VIEWS = "data/review/triage-v2/source-views.json"
PRIVATE_FILES = {COMPLETED, HISTORY, LATEST, DATASET, PACKAGE}
ROUND = "validation-adjudication"
LEVELS = {"site", "subsite", "district_area", "operation_complex", "unknown"}


def implementation_hashes(root: Path) -> dict:
    return {
        path.relative_to(root).as_posix(): sha256(path)
        for path in [*sorted((root / "pipeline/preview").glob("*.py")), root / "uv.lock"]
    }


def _read(root: Path, relative: str):
    return json.loads((root / relative).read_bytes())


def _bound(root: Path, receipt: dict, relative: str) -> bytes:
    content = (root / relative).read_bytes()
    if receipt["artifacts"][relative] != {"sha256": digest(content), "bytes": len(content)}:
        raise ValueError(f"Accepted parent artifact changed: {relative}")
    return content


def append_adjudication(history: dict, rows: list[dict], csv_sha256: str) -> tuple[dict, dict]:
    """Append exact CSV fields, then derive latest answers without changing any labels."""
    if history.get("schema_version") != "decision-history-v1":
        raise ValueError("Unrecognized human history")
    if any(item["round_id"] == ROUND for item in history["rounds"]):
        raise ValueError("Adjudication must derive from the frozen pre-adjudication history")
    events = list(history["events"])
    by_pair = defaultdict(list)
    for event in events:
        by_pair[event["pair_id"]].append(event)
    seen = set()
    for row in rows:
        if set(row) != set(HEADERS) or not row["label"] or row["pair_id"] in seen:
            raise ValueError("Adjudication requires exact, unique completed CSV rows")
        seen.add(row["pair_id"])
        prior = by_pair.get(row["pair_id"])
        if not prior or any(row[key] != prior[-1][key] for key in HEADERS[1:3]):
            raise ValueError("Adjudication changed a preserved comparison or its orientation")
        event = {**row, "round_id": ROUND, "source_csv_sha256": csv_sha256}
        event["event_id"] = "decision_" + digest(json_bytes(event))
        events.append(event)
        prior.append(event)
    updated = {
        **history,
        "rounds": [
            *history["rounds"],
            {"round_id": ROUND, "source_csv_sha256": csv_sha256, "event_count": len(rows)},
        ],
        "events": events,
    }
    decisions = []
    for _pair, items in sorted(by_pair.items()):
        newest = items[-1]
        decisions.append(
            {
                **{key: newest[key] for key in HEADERS},
                "round_id": newest["round_id"],
                "latest_event_id": newest["event_id"],
                "history_event_ids": [item["event_id"] for item in items],
            }
        )
    latest = {
        "schema_version": "latest-human-decisions-v1",
        "history_sha256": digest(json_bytes(updated)),
        "decisions": decisions,
        "counts": dict(sorted(Counter(row["label"] for row in decisions).items())),
        "policy": "Latest human comparison answers only; no canonical identities or merges.",
    }
    return updated, latest


def safe_url(value) -> str:
    """Allow bounded HTTPS citation URLs without credentials or control characters."""
    if not isinstance(value, str) or len(value) > 2048 or re.search(r"[\s\x00-\x1f]", value):
        return ""
    try:
        parsed = urlsplit(value)
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or "\\" in value
        ):
            return ""
        _ = parsed.port
    except ValueError:
        return ""
    return value


def _strings(value) -> list[str]:
    if not isinstance(value, list):
        return []
    return list(dict.fromkeys(item for item in value if isinstance(item, str) and item.strip()))


def _text(value) -> str:
    return str(value).strip() if isinstance(value, str | int | float) else ""


def _status(value) -> str:
    if isinstance(value, dict):
        return "; ".join(f"{key}: {_text(item)}" for key, item in value.items() if _text(item))
    return _text(value)


def project_dataset(records: dict, registries: dict, latest: dict, source_view_pairs: int) -> dict:
    """Keep one display observation per input record, even for reviewed same-site pairs."""
    reviewed = {
        row[key] for row in latest["decisions"] for key in ("left_record_id", "right_record_id")
    }
    projected, retrieved = [], defaultdict(set)
    for record_id, row in sorted(records.items()):
        if row["record_id"] != record_id:
            raise ValueError("Source record key and embedded ID disagree")
        source = registries[row["source_id"]]
        lat, lon = row["lat"], row["lon"]
        if (
            type(lat) not in (int, float)
            or type(lon) not in (int, float)
            or not math.isfinite(lat)
            or not math.isfinite(lon)
            or not -90 <= lat <= 90
            or not -180 <= lon <= 180
        ):
            raise ValueError("Preview requires valid finite geographic coordinates")
        codes = _strings(row.get("country_iso3s"))
        country = pycountry.countries.get(alpha_3=codes[0]) if len(codes) == 1 else None
        provenance = row.get("provenance") or {}
        when = provenance.get("retrieved_at")
        if isinstance(when, str) and when:
            retrieved[row["source_id"]].add(when)
        url = safe_url(row.get("site_url")) or safe_url(source.get("url"))
        if not url:
            raise ValueError("A preview source requires a safe HTTPS citation")
        level = row.get("entity_level")
        if level not in LEVELS:
            raise ValueError("Unknown entity classification")
        families = _strings(row.get("pilot_families"))
        if not families or set(families) - {"REE", "Ni", "Co"}:
            raise ValueError("Preview input is outside the eligible pilot families")
        evidence = f"Source observation; {row['source_id']}"
        table = _text(provenance.get("source_table"))
        source_row = provenance.get("source_row")
        if table:
            evidence += f"; table {table}"
        if isinstance(source_row, int | str):
            evidence += f"; row {source_row}"
        if provenance.get("raw_sha256"):
            evidence += f"; archive SHA-256 {provenance['raw_sha256']}"
        projected.append(
            {
                "id": digest(record_id.encode()),
                "record_id": record_id,
                "source_id": row["source_id"],
                "name": _text(row.get("display_name")) or _text(row.get("raw_name")),
                "country": country.alpha_3 if country else "",
                "country_name": country.name if country else "",
                "minerals": sorted(families),
                "entity_level": level,
                "lat": lat,
                "lon": lon,
                "location_precision": row.get("location_precision"),
                "status": _status(row.get("raw_status")),
                "deposit_types": _strings(row.get("deposit_types")),
                "aliases": _strings(row.get("identity_aliases")),
                "context_names": _strings(row.get("context_names")),
                "source_url": url,
                "source_title": source["name"],
                "source_date": _text(provenance.get("source_currency")) or None,
                "source_license": source["license"],
                "provenance": evidence,
                "identity_review": (
                    "Has reviewed comparisons"
                    if record_id in reviewed
                    else "Identity not consolidated"
                ),
            }
        )
    source_ids = sorted({row["source_id"] for row in projected})
    sources = []
    for source_id in source_ids:
        source = registries[source_id]
        url = safe_url(source.get("url"))
        if not url or source.get("source_id") != source_id:
            raise ValueError("Source registry identity or citation is invalid")
        sources.append(
            {
                "id": source_id,
                "title": source["name"],
                "url": url,
                "license": source["license"],
                "redistribution_ok": source["redistribution_ok"] is True,
                "retrieved_at": max(retrieved[source_id]) if retrieved[source_id] else None,
            }
        )
    return {
        "schema_version": "atlas-private-preview-v1",
        "records": projected,
        "sources": sources,
        "summary": {
            "record_count": len(projected),
            "source_count": len(sources),
            "known_entity_level_counts": dict(
                sorted(Counter(row["entity_level"] for row in projected).items())
            ),
            "publisher_view_duplicate_pairs_retained": source_view_pairs,
            "identity_merges": 0,
            "record_policy": "Source observations; duplicates retained; not unique sites.",
        },
        "data_as_of": None,
        "release_tag": "2026.11.01-preview",
    }


def build(root: Path, adjudication_path: Path) -> tuple[dict[str, bytes], dict]:
    """Build deterministic bytes; no files are accepted or overwritten here."""
    parent = check_reconciliation(root)
    packet = load_packet(root, parent, "adjudication")
    csv_bytes = adjudication_path.read_bytes()
    validation = validate_export(adjudication_path, packet, complete=True)
    if adjudication_path.read_bytes() != csv_bytes:
        raise ValueError("Adjudication changed during import")
    rows = list(csv.DictReader(io.StringIO(csv_bytes.decode("utf-8-sig"), newline="")))
    prior = json.loads(_bound(root, parent, PARENT_HISTORY))
    history, latest = append_adjudication(prior, rows, digest(csv_bytes))
    triage = _read(root, TRIAGE_RECEIPT)
    records = json.loads(_bound(root, triage, RECORDS))
    views = json.loads(_bound(root, triage, VIEWS))
    registries, registry_hashes = {}, {}
    for source_id in sorted({row["source_id"] for row in records.values()}):
        if not re.fullmatch(r"[a-z0-9_]+", source_id):
            raise ValueError("Unsafe source registry identifier")
        relative = f"sources/{source_id}.yaml"
        content = (root / relative).read_bytes()
        registries[source_id] = json.loads(content)
        registry_hashes[relative] = digest(content)
    dataset = project_dataset(records, registries, latest, len(views["pairs"]))
    dataset_bytes = (
        json.dumps(
            dataset, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
        )
        + "\n"
    ).encode()
    outputs = {
        COMPLETED: csv_bytes,
        HISTORY: json_bytes(history),
        LATEST: json_bytes(latest),
        DATASET: dataset_bytes,
    }
    review = {
        "adjudication_csv_sha256": digest(csv_bytes),
        "adjudication_sample_sha256": packet["sample_sha256"],
        "imported_answers": len(rows),
        "imported_label_counts": dict(sorted(Counter(row["label"] for row in rows).items())),
        "all_adjudication_cases_reviewed": validation["all_cases_reviewed"],
        "previous_events_preserved": len(prior["events"]),
        "history_events": len(history["events"]),
        "unique_comparisons": len(latest["decisions"]),
        "latest_label_counts": latest["counts"],
        "unresolved_comparisons": sum(latest["counts"].get(label, 0) for label in UNRESOLVED),
        "formal_new_cases_remaining": parent["selection"]["new_units"],
        "formal_gold_set_complete": False,
    }
    package = {
        "schema_version": "private-preview-package-v1",
        "dataset_sha256": digest(dataset_bytes),
        "dataset_bytes": len(dataset_bytes),
        "record_policy": dataset["summary"]["record_policy"],
        "source_registry_sha256": registry_hashes,
        "privacy": "Local private preview; not a redistribution or canonical release.",
    }
    outputs[PACKAGE] = json_bytes(package)
    receipt = {
        "schema_version": "private-preview-audit-v1",
        "status": "private_preview_ready_identity_validation_pending",
        "implementation": implementation_hashes(root),
        "parent_receipt_sha256": sha256(root / PARENT_RECEIPT),
        "triage_receipt_sha256": sha256(root / TRIAGE_RECEIPT),
        "source_registry_sha256": registry_hashes,
        "review": review,
        "dataset": {
            **dataset["summary"],
            "source_record_counts": dict(
                sorted(Counter(row["source_id"] for row in records.values()).items())
            ),
            "data_as_of": None,
            "release_tag": dataset["release_tag"],
        },
        "model_fitted": False,
        "auto_merges": 0,
        "canonical_rows_added": 0,
        "published_rows_added": 0,
        "privacy": "Public receipt contains aggregate counts and checksums only; data stays local.",
        "artifacts": {
            name: {"sha256": digest(content), "bytes": len(content)}
            for name, content in sorted(outputs.items())
        },
    }
    validate_receipt(receipt)
    return outputs, receipt


def validate_receipt(receipt: dict) -> None:
    review, dataset = receipt["review"], receipt["dataset"]
    counts = review["latest_label_counts"]
    if (
        receipt["schema_version"] != "private-preview-audit-v1"
        or receipt["status"] != "private_preview_ready_identity_validation_pending"
        or receipt["model_fitted"] is not False
        or any(
            receipt[key] != 0
            for key in ("auto_merges", "canonical_rows_added", "published_rows_added")
        )
        or review["formal_gold_set_complete"] is not False
        or review["all_adjudication_cases_reviewed"] is not True
        or review["history_events"]
        != review["previous_events_preserved"] + review["imported_answers"]
        or sum(counts.values()) != review["unique_comparisons"]
        or sum(review["imported_label_counts"].values()) != review["imported_answers"]
        or review["unresolved_comparisons"] != sum(counts.get(label, 0) for label in UNRESOLVED)
        or dataset["record_count"] != sum(dataset["source_record_counts"].values())
        or dataset["record_count"] != sum(dataset["known_entity_level_counts"].values())
        or dataset["source_count"] != len(dataset["source_record_counts"])
        or dataset["identity_merges"] != 0
        or dataset["data_as_of"] is not None
        or set(receipt["artifacts"]) != PRIVATE_FILES
    ):
        raise ValueError("Invalid private preview aggregate receipt")
    for value in receipt["artifacts"].values():
        if (
            set(value) != {"sha256", "bytes"}
            or not re.fullmatch(r"[0-9a-f]{64}", value["sha256"])
            or type(value["bytes"]) is not int
            or value["bytes"] < 1
        ):
            raise ValueError("Invalid preview artifact checksum")


def prepare(root: Path, adjudication_path: Path | None = None) -> dict:
    outputs, receipt = build(root, adjudication_path or root / COMPLETED)
    outputs[RECEIPT] = json_bytes(receipt)
    with tempfile.TemporaryDirectory(prefix=".private-preview-", dir=root / "data") as temporary:
        staged = []
        for name, content in outputs.items():
            path = Path(temporary) / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
            staged.append((path, root / name))
        accept_outputs(staged)
    return receipt


def check(root: Path, *, local=False) -> dict:
    """Offline CI checks the public receipt; --local also reproduces every private artifact."""
    parent = check_reconciliation(root)
    triage = _read(root, TRIAGE_RECEIPT)
    receipt = _read(root, RECEIPT)
    validate_receipt(receipt)
    if (
        receipt["implementation"] != implementation_hashes(root)
        or receipt["parent_receipt_sha256"] != sha256(root / PARENT_RECEIPT)
        or receipt["triage_receipt_sha256"] != sha256(root / TRIAGE_RECEIPT)
        or receipt["review"]["adjudication_sample_sha256"]
        != parent["packets"]["adjudication"]["sample_sha256"]
        or receipt["review"]["imported_answers"] != parent["packets"]["adjudication"]["cases"]
        or receipt["review"]["formal_new_cases_remaining"] != parent["selection"]["new_units"]
        or receipt["review"]["previous_events_preserved"] != parent["ledger"]["history_events"]
        or receipt["review"]["unique_comparisons"] != parent["ledger"]["unique_reviewed_pairs"]
        or receipt["dataset"]["record_count"] != triage["eligible_records"]
        or receipt["dataset"]["known_entity_level_counts"] != triage["entity_levels"]
        or receipt["dataset"]["publisher_view_duplicate_pairs_retained"]
        != triage["source_view_pairs"]
        or set(receipt["source_registry_sha256"])
        != {f"sources/{key}.yaml" for key in receipt["dataset"]["source_record_counts"]}
    ):
        raise ValueError("Preview implementation or frozen parent changed")
    for name, expected in receipt["source_registry_sha256"].items():
        if not re.fullmatch(r"sources/[a-z0-9_]+\.yaml", name) or sha256(root / name) != expected:
            raise ValueError("Preview source registry changed")
    if local:
        outputs, rebuilt = build(root, root / COMPLETED)
        if rebuilt != receipt:
            raise ValueError("Rebuilt preview receipt changed")
        for name, content in outputs.items():
            if (root / name).read_bytes() != content:
                raise ValueError(f"Private preview artifact changed: {name}")
    return receipt
