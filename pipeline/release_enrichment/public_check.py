"""Check public release receipts against published code and private-input commitments."""

import argparse
import json
import re
from pathlib import Path

from pipeline.normalize.core import sha256
from pipeline.release_enrichment.common import IDENTITY, PILOT, RELEASE, STAGES, load
from pipeline.release_identity.core import check as check_identity

PACKAGE = "pipeline/release_enrichment"
PINS = f"{PACKAGE}/public-checksums.json"
AUTHORITY = f"{PACKAGE}/site_statements.json"
CONTEXT_SOURCES = {"usgs_mcs_2026", "oecd_export", "aiddata_transition_v2"}
PRIVATE_FILES = {
    "maturity": {
        "sites.json",
        "reviewed-sites.json",
        "corrections.json",
        "jasansky-reserve-audit.json",
        "reserve-independent-review.json",
        "mapping.json",
        "spot-check-input.json",
        "spot-check-assessment.json",
        "spot-check-results.json",
        "primary/mcs2026-nickel.pdf",
        "primary/mcs2026-rare-earths.pdf",
        "primary/arafura-annual-2026.pdf",
    },
    "context": {
        "country_commodity.json",
        "minerals.json",
        "export_restrictions.json",
        "china_official_finance.json",
        "mcs-revisions.json",
        "primary/mcs2026-nickel.pdf",
        "primary/mcs2026-rare-earths.pdf",
        "primary/mcs2026-cobalt.pdf",
    },
}


def valid_hash(value):
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def verify_code(root, stage, receipt, pins):
    parts = {"__init__.py", "__main__.py", "common.py", stage + ".py", "mcs_chapters.json"}
    source_ids = {s["source_id"] for s in load(root / PILOT)["sources"]}
    if stage == "maturity":
        parts.update({"corrections.py", "site_statements.json"})
        source_ids -= CONTEXT_SOURCES
    else:
        source_ids = CONTEXT_SOURCES
    expected = {
        "uv.lock",
        *(f"{PACKAGE}/{part}" for part in parts),
        *(f"sources/{s}.yaml" for s in source_ids),
    }
    if set(receipt["implementation"]) != expected:
        raise ValueError("Sealed implementation file set changed")
    for name in sorted(expected):
        if name == AUTHORITY:
            # This input stays private. CI compares two immutable commitments to
            # its bytes. Local maturity --check --local reads and rebuilds it.
            actual = pins[AUTHORITY]
        else:
            actual = sha256(root / name)
        if actual != receipt["implementation"][name]:
            raise ValueError(f"Sealed implementation checksum changed: {name}")


def verify_artifacts(root, stage, receipt):
    directory = f"{RELEASE}/{stage}/"
    _, report = STAGES[stage]
    artifacts = receipt["artifacts"]
    if set(artifacts) != {report, *(directory + name for name in PRIVATE_FILES[stage])}:
        raise ValueError("Unexpected sealed artifact list")
    for entry in artifacts.values():
        if (
            not isinstance(entry, dict)
            or set(entry) != {"bytes", "sha256"}
            or type(entry["bytes"]) is not int
            or entry["bytes"] < 1
            or not valid_hash(entry["sha256"])
        ):
            raise ValueError("Malformed artifact commitment")
    source = root / report
    if artifacts[report] != {"bytes": source.stat().st_size, "sha256": sha256(source)}:
        raise ValueError("Aggregate report changed")
    return {name: v["sha256"] for name, v in artifacts.items()}


def checked_counts(counts, total):
    return (
        isinstance(counts, dict)
        and bool(counts)
        and all(type(n) is int and n > 0 for n in counts.values())
        and sum(counts.values()) == total
    )


def check(root: Path, stage: str):
    pins = load(root / PINS)
    if not all(valid_hash(v) for v in pins.values()):
        raise ValueError("Malformed public checksum manifest")
    identity = check_identity(root)
    name, _ = STAGES[stage]
    if sha256(root / name) != pins.get(name):
        raise ValueError("Sealed receipt differs from published commitment")
    receipt = load(root / name)
    if receipt["status"] != "accepted" or receipt["pilot_receipt_sha256"] != sha256(root / PILOT):
        raise ValueError("Enrichment status or pilot parent changed")
    verify_code(root, stage, receipt, pins)
    artifacts = verify_artifacts(root, stage, receipt)
    folder = f"{RELEASE}/{stage}/"
    if stage == "maturity":
        size = identity["counts"]["site_entries"]
        if (
            receipt["schema_version"] != "release-maturity-v1"
            or receipt["step"] != 7
            or receipt["parent_receipt_sha256"] != sha256(root / IDENTITY)
            or receipt["sites"] != size
            or not checked_counts(receipt["rung_counts"], size)
            or not checked_counts(receipt["reviewed_rung_counts"], size)
            or not checked_counts(receipt["status_counts"], size)
            or set(receipt["rung_counts"]) != {"1", "2", "3", "4", "5"}
            or set(receipt["reviewed_rung_counts"]) != {"1", "2", "3", "4", "5"}
            or receipt["spot_check_passed"] is not True
            or receipt["spot_check_cases"] != 30
            or not 27 <= receipt["spot_check_agreements"] <= 30
            or receipt["scored_sites_sha256"] != artifacts[folder + "sites.json"]
            or receipt["assessment_sha256"] != artifacts[folder + "spot-check-assessment.json"]
            or receipt["reserve_review_sha256"]
            != artifacts[folder + "reserve-independent-review.json"]
            or not valid_hash(receipt["sample_sha256"])
            or receipt["post_assessment_corrections"]
            != (receipt["rung_counts"]["1"] - receipt["reviewed_rung_counts"]["1"])
            or receipt["post_assessment_corrections"]
            != (receipt["reviewed_rung_counts"]["2"] - receipt["rung_counts"]["2"])
        ):
            raise ValueError("Maturity acceptance or coverage differs")
        docs = {p.split("/primary/", 1)[1]: v for p, v in artifacts.items() if "/primary/" in p}
        if receipt["verified_document_sha256"] != docs:
            raise ValueError("Verified maturity document commitments changed")
    else:
        check(root, "maturity")
        chapters = load(root / PACKAGE / "mcs_chapters.json")
        if (
            receipt["schema_version"] != "release-country-context-v1"
            or receipt["step"] != 8
            or receipt["parent_receipt_sha256"] != sha256(root / STAGES["maturity"][0])
            or receipt["top_producers_verified"] != {"Co": "COD", "Ni": "IDN", "REE": "CHN"}
            or receipt["rounded_share_checks_passed"] != 6
            or receipt["oecd_coverage"] != [2009, 2024]
            or receipt["finance_site_links"] != 0
            or receipt["counts"]["minerals"] != 3
            or receipt["chapter_sha256"] != {k: c["sha256"] for k, c in chapters.items()}
            or any(
                artifacts[folder + "primary/" + c["file"]] != c["sha256"] for c in chapters.values()
            )
        ):
            raise ValueError("Country-context acceptance or verification differs")
    return receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("stage", choices=STAGES)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    args = parser.parse_args()
    result = check(args.root.resolve(), args.stage)
    print(
        json.dumps(
            {"stage": args.stage, "status": result["status"], "verification": "public_receipt"}
        )
    )


if __name__ == "__main__":
    main()
