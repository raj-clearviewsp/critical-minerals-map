"""Reproducible terminology coverage from verified originals, without staging rows."""

from __future__ import annotations

import csv
import io
import json
import re
from collections import Counter, defaultdict
from pathlib import Path

from pipeline.fetch.core import verify_snapshot
from pipeline.scaffold import digest, json_bytes
from pipeline.vocab.core import Vocabulary, VocabularyError

REQUIRED_SOURCES = {
    "ga_global_ree",
    "usgs_critical_list_2025",
    "usgs_mcs_2026",
    "jasansky",
    "usgs_global_ree",
    "wapha",
    "gseu_crm",
    "usgs_laterites",
    "ga_critical_2025",
    "icmm",
    "usgs_mrds",
    "oecd_export",
    "ga_critical_2025_arcgis",
    "usgs_global_critical",
    "aiddata_transition_v2",
    "usgs_us_critical",
}


PILOT_NAMES = {
    "Ni": r"nickel|ferronickel|ni(?:o|so4)?|ni_[lrs]",
    "Co": r"cobalt|co(?:o)?",
    "REE": r"rare[ -]?earths?|ree(?:2o3)?|[lh]ree|treo|re2o3|reo|lanthanides?|"
    r"cerium|dysprosium|erbium|europium|gadolinium|holmium|lanthanum|lutetium|"
    r"neodymium|praseodymium|promethium|samarium|scandium|terbium|thulium|ytterbium|yttrium|"
    r"(?:ce|dy|er|eu|gd|ho|la|lu|nd|pr|pm|sm|sc|tb|tm|yb|y)(?:2o3|4o7|6o11|o2)?",
}
DETECTORS = {
    code: re.compile(r"(?<![A-Za-z0-9_])(?:" + names + r")(?![A-Za-z0-9_])", re.I)
    for code, names in PILOT_NAMES.items()
}


def pilot_families(vocab: Vocabulary, codes: list[str]) -> set[str]:
    result = set()
    for code in codes:
        if code in ("Ni", "Co"):
            result.add(code)
        elif vocab.entries["commodities"][code]["group"] in ("rare_earth", "rare_earth_group"):
            result.add("REE")
    return result


def expected_families(vocab: Vocabulary, observation: dict) -> set[str]:
    if observation["kind"] != "commodity":
        return set()
    source, field, raw = (observation[k] for k in ("source_id", "field", "raw_value"))
    # HS codes have positive and exclusion language. Only reviewed codebook mappings
    # establish pilot associations; a keyword in an exclusion is never an inclusion.
    families = (
        set()
        if source == "oecd_export" or observation.get("value_origin") == "column_header"
        else {code for code, pattern in DETECTORS.items() if pattern.search(raw)}
    )
    for expected in vocab.rules.get("pilot_code_expectations", []):
        if (expected["source_id"], expected["field"], expected["raw_value"]) == (
            source,
            field,
            raw,
        ):
            families.update(pilot_families(vocab, expected["codes"]))
    return families


def render_csv(rows: list[dict], fields: list[str]) -> bytes:
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=fields, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return stream.getvalue().encode()


def write_changed(path: Path, content: bytes) -> None:
    if path.exists() and path.read_bytes() == content:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_bytes(content)
    temporary.replace(path)


def expectation_checks(vocab: Vocabulary) -> dict:
    """Check publisher dictionary codes, including those absent from current cells."""
    cases = vocab.rules["pilot_code_expectations"]
    failures = []
    for case in cases:
        actual = (
            set(
                vocab.resolve("commodity", case["source_id"], case["field"], case["raw_value"])[
                    "codes"
                ]
            )
            & vocab.pilot_codes
        )
        expected = set(case["codes"]) & vocab.pilot_codes
        if expected != actual:
            failures.append(
                {
                    "source_id": case["source_id"],
                    "field": case["field"],
                    "raw_value": case["raw_value"],
                    "expected": sorted(expected),
                    "actual": sorted(actual),
                }
            )
    return {"cases": len(cases), "failures": failures}


def summary(rows: list[dict]) -> dict:
    by_source = defaultdict(Counter)
    for row in rows:
        counter = by_source[row["source_id"]]
        counter["distinct_field_values"] += 1
        counter["observed_cells"] += row["count"]
        counter["pilot_field_values"] += bool(row["pilot_expected"])
        counter["pilot_unmapped"] += bool(row["pilot_missing"])
        counter["unmapped_field_values"] += bool(json.loads(row["unmapped"]))
    return dict(by_source)


def definition_hashes(root: Path) -> dict:
    paths = [
        *sorted((root / "sources/vocab").glob("*.yaml")),
        *sorted((root / "pipeline/vocab").glob("*.py")),
        root / "schemas/vocabulary.schema.json",
        root / "uv.lock",
    ]
    return {p.relative_to(root).as_posix(): digest(p.read_bytes()) for p in paths}


def resolve_observations(vocab: Vocabulary, observations: list[dict]) -> list[dict]:
    result = []
    for row in observations:
        resolution = vocab.resolve(row["kind"], row["source_id"], row["field"], row["raw_value"])
        expected = expected_families(vocab, row)
        actual = pilot_families(vocab, resolution["codes"]) if row["kind"] == "commodity" else set()
        result.append(
            {
                k: row[k]
                for k in ("source_id", "file", "table", "field", "kind", "raw_value", "count")
            }
            | {
                "value_origin": row.get("value_origin", "cell"),
                "codes": "|".join(resolution["codes"]),
                "unmapped": json.dumps(resolution["unmapped"], ensure_ascii=False),
                "reason": resolution["reason"],
                "pilot_expected": "|".join(sorted(expected)),
                "pilot_missing": "|".join(sorted(expected - actual)),
            }
        )
    return sorted(
        result,
        key=lambda x: tuple(
            str(x[k]) for k in ("source_id", "file", "table", "field", "raw_value")
        ),
    )


def audit(root: Path) -> dict:
    from pipeline.vocab.authority import validate_us_list
    from pipeline.vocab.geology import extract as extract_geology
    from pipeline.vocab.tabular import extract as extract_tabular

    vocab = Vocabulary(root)
    vocab.validate_rules()
    snapshots, inputs = {}, {}
    for source_dir in sorted((root / "data/raw").iterdir()):
        if not source_dir.is_dir():
            continue
        dates = sorted(source_dir.glob("????-??-??"))
        if not dates:
            continue
        snapshot = dates[-1]
        manifest = verify_snapshot(snapshot)
        snapshots[manifest["source_id"]] = {
            "snapshot": snapshot.relative_to(root).as_posix(),
            "manifest_sha256": digest((snapshot / "manifest.json").read_bytes()),
        }
        for entry in manifest["files"]:
            relative = (snapshot / entry["path"]).relative_to(root / "data/raw").as_posix()
            inputs[relative] = entry["sha256"]
    if set(snapshots) != REQUIRED_SOURCES:
        raise VocabularyError("Raw source set differs from the reviewed sixteen-source inventory")
    audits = [extract_geology(root), extract_tabular(root)]
    list_path = (
        root / snapshots["usgs_critical_list_2025"]["snapshot"] / "critical-minerals-list-2025.html"
    )
    us_list_names = validate_us_list(list_path.read_text(), vocab.entries["commodities"])
    observations = [o for data in audits for o in data["observations"]]
    resolved = resolve_observations(vocab, observations)
    if not resolved:
        raise VocabularyError("No source observations; an empty scan is not coverage")
    fields = list(resolved[0])
    observation_csv = render_csv(resolved, fields)
    unmapped = [r for r in resolved if json.loads(r["unmapped"])]
    expectations = expectation_checks(vocab)
    profiles = {
        "tables": [t for a in audits for t in a["tables"]],
        "notes": [n for a in audits for n in a["notes"]],
        "code_labels": [c for a in audits for c in a.get("code_labels", [])],
        "lookups": [c for a in audits for c in a.get("lookups", [])],
    }
    receipt = {
        "schema_version": "vocabulary-audit-v1",
        "phase": 0,
        "step": 3,
        "sources": snapshots,
        "input_files": inputs,
        "vocab_files": {
            p.name: digest(p.read_bytes()) for p in sorted((root / "sources/vocab").glob("*.yaml"))
        },
        "definition_hashes": definition_hashes(root),
        "source_summary": summary(resolved),
        "observations_sha256": digest(observation_csv),
        "profiles_sha256": digest(json_bytes(profiles)),
        "unmapped_sha256": digest(render_csv(unmapped, fields)),
        "expectations": expectations,
        "pilot_unmapped": sum(bool(r["pilot_missing"]) for r in resolved),
        "status": "pass"
        if not any(r["pilot_missing"] for r in resolved) and not expectations["failures"]
        else "needs_review",
        "normalized_rows": 0,
        "us_list_names": us_list_names,
    }
    write_changed(root / "reports/vocabulary-observations.csv", observation_csv)
    write_changed(root / "review/unmapped-vocabulary.csv", render_csv(unmapped, fields))
    write_changed(root / "reports/vocabulary-source-fields.json", json_bytes(profiles))
    write_changed(root / "reports/vocabulary-audit.json", json_bytes(receipt))
    return receipt


def check(root: Path) -> dict:
    vocab = Vocabulary(root)
    vocab.validate_rules()
    receipt = json.loads((root / "reports/vocabulary-audit.json").read_text())
    if set(receipt["sources"]) != REQUIRED_SOURCES:
        raise VocabularyError("Retained audit does not cover the required sources")
    if definition_hashes(root) != receipt["definition_hashes"]:
        raise VocabularyError("Vocabulary or audit implementation changed; rerun the source audit")
    for filename, checksum in receipt["vocab_files"].items():
        if digest((root / "sources/vocab" / filename).read_bytes()) != checksum:
            raise VocabularyError("Vocabulary changed; rerun the full source audit")
    path = root / "reports/vocabulary-observations.csv"
    if digest(path.read_bytes()) != receipt["observations_sha256"]:
        raise VocabularyError("Observed terminology differs from its audit receipt")
    with path.open(newline="") as stream:
        observations = list(csv.DictReader(stream))
    for row in observations:
        row["count"] = int(row["count"])
        if row["count"] < 1:
            raise VocabularyError("Observation counts must be positive")
    recomputed = resolve_observations(vocab, observations)
    expectations = expectation_checks(vocab)
    if (
        not observations
        or recomputed != observations
        or receipt["pilot_unmapped"]
        or receipt["status"] != "pass"
        or expectations["failures"]
        or expectations != receipt["expectations"]
        or summary(observations) != receipt["source_summary"]
        or any(r["pilot_missing"] for r in recomputed)
    ):
        raise VocabularyError("Source terminology coverage failed or needs review")
    for relative, checksum in [
        ("reports/vocabulary-source-fields.json", receipt["profiles_sha256"]),
        ("review/unmapped-vocabulary.csv", receipt["unmapped_sha256"]),
    ]:
        if digest((root / relative).read_bytes()) != checksum:
            raise VocabularyError("Audit evidence or review queue differs from its receipt")
    return receipt
