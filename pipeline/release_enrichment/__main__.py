"""Build or verify dated release enrichment for Step 7 and Step 8."""

import argparse
import json
import re
from pathlib import Path

from pipeline.normalize.core import sha256
from pipeline.release_enrichment import maturity
from pipeline.release_enrichment.common import IDENTITY, PILOT, STAGES, checksums, load
from pipeline.scaffold import json_bytes


def check(root: Path, stage: str, local=False):
    receipt = load(root / STAGES[stage][0])
    parent = IDENTITY if stage == "maturity" else STAGES["maturity"][0]
    if (
        receipt["parent_receipt_sha256"] != sha256(root / parent)
        or receipt["pilot_receipt_sha256"] != sha256(root / PILOT)
        or receipt["implementation"] != checksums(root, stage)
    ):
        raise ValueError("Release enrichment parent or implementation changed")
    if stage == "maturity":
        valid = (
            receipt["status"] == "accepted"
            and receipt["spot_check_passed"] is True
            and receipt["spot_check_cases"] == 30
            and 27 <= receipt["spot_check_agreements"] <= 30
            and sum(receipt["rung_counts"].values()) == receipt["sites"]
        )
    else:
        from pipeline.release_enrichment import context

        check(root, "maturity")
        valid = (
            receipt["status"] == "accepted"
            and receipt["step"] == 8
            and receipt["top_producers_verified"] == context.LEADERS
            and receipt["rounded_share_checks_passed"] == 6
            and receipt["oecd_coverage"] == [2009, 2024]
            and receipt["finance_site_links"] == 0
        )
    if not valid:
        raise ValueError("Release enrichment acceptance criteria failed")
    stage_dir = f"data/releases/2026.11.04-v0/{stage}"
    filenames = (
        (
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
        )
        if stage == "maturity"
        else (
            "country_commodity.json",
            "minerals.json",
            "export_restrictions.json",
            "china_official_finance.json",
            "mcs-revisions.json",
            "primary/mcs2026-nickel.pdf",
            "primary/mcs2026-rare-earths.pdf",
            "primary/mcs2026-cobalt.pdf",
        )
    )
    if set(receipt["artifacts"]) != {
        STAGES[stage][1],
        *(stage_dir + "/" + name for name in filenames),
    }:
        raise ValueError("Release enrichment artifact list differs")
    if stage == "maturity":
        if (
            receipt.get("schema_version") != "release-maturity-v1"
            or receipt.get("step") != 7
            or type(receipt.get("sites")) is not int
            or set(receipt["rung_counts"]) - {"1", "2", "3", "4", "5"}
            or any(type(v) is not int or v < 1 for v in receipt["rung_counts"].values())
            or any(type(v) is not int or v < 1 for v in receipt["status_counts"].values())
            or sum(receipt["status_counts"].values()) != receipt["sites"]
            or sum(receipt["reviewed_rung_counts"].values()) != receipt["sites"]
            or receipt["scored_sites_sha256"]
            != receipt["artifacts"][stage_dir + "/sites.json"]["sha256"]
            or receipt["reserve_review_sha256"]
            != receipt["artifacts"][stage_dir + "/reserve-independent-review.json"]["sha256"]
            or receipt["assessment_sha256"]
            != receipt["artifacts"][stage_dir + "/spot-check-assessment.json"]["sha256"]
            or not re.fullmatch(r"[0-9a-f]{64}", receipt["sample_sha256"])
        ):
            raise ValueError("Release maturity coverage or assessment changed")
        expected_docs = {
            name.split("/", 1)[1]: receipt["artifacts"][stage_dir + "/" + name]["sha256"]
            for name in filenames
            if name.startswith("primary/")
        }
        if receipt["verified_document_sha256"] != expected_docs:
            raise ValueError("Maturity verification documents differ")
    else:
        chapters = load(context.CHAPTER_FILE)
        if (
            receipt.get("schema_version") != "release-country-context-v1"
            or receipt["chapter_sha256"] != {k: c["sha256"] for k, c in chapters.items()}
            or any(
                receipt["artifacts"][stage_dir + "/primary/" + c["file"]]["sha256"] != c["sha256"]
                for c in chapters.values()
            )
            or any(type(v) is not int or v < 1 for v in receipt["counts"].values())
            or receipt["counts"]["minerals"] != 3
        ):
            raise ValueError("Country context coverage or verification documents differ")
    for name, artifact in receipt["artifacts"].items():
        if (
            set(artifact) != {"sha256", "bytes"}
            or type(artifact["bytes"]) is not int
            or artifact["bytes"] < 1
            or not re.fullmatch(r"[0-9a-f]{64}", artifact["sha256"])
        ):
            raise ValueError("Malformed release enrichment artifact digest")
        if local or name == STAGES[stage][1]:
            path = root / name
            if path.stat().st_size != artifact["bytes"] or sha256(path) != artifact["sha256"]:
                raise ValueError(f"Changed release enrichment artifact: {name}")
    if local:
        if stage == "maturity":
            files, summary = maturity.build(
                root, root / maturity.FOLDER / "spot-check-assessment.json"
            )
        else:
            files, summary = context.build(root, root / context.FOLDER / "primary")
        if any(summary[k] != receipt[k] for k in summary) or set(files) != set(
            receipt["artifacts"]
        ):
            raise ValueError("Rebuilt release enrichment differs from accepted receipt")
        if any((root / k).read_bytes() != v for k, v in files.items()):
            raise ValueError("Rebuilt release enrichment differs from accepted bytes")
    return receipt


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("stage", choices=STAGES)
    p.add_argument("--root", type=Path, default=Path.cwd())
    p.add_argument("--assessment", type=Path)
    p.add_argument("--correction-review", type=Path)
    p.add_argument("--primary-documents", type=Path)
    p.add_argument("--spot-check-packet", type=Path)
    p.add_argument("--check", action="store_true")
    p.add_argument("--local", action="store_true")
    args = p.parse_args()
    root = args.root.resolve()
    if args.check:
        result = check(root, args.stage, args.local)
    elif args.spot_check_packet and args.stage == "maturity":
        sites, by_site = maturity.inputs(root)
        packet = maturity.spot_packet(sites, by_site)
        raw = json_bytes(packet)
        if args.spot_check_packet.exists() and args.spot_check_packet.read_bytes() != raw:
            raise ValueError("Existing blind packet differs")
        if not args.spot_check_packet.exists():
            args.spot_check_packet.write_bytes(raw)
        result = {"cases": len(packet["cases"]), "sample_sha256": packet["sample_sha256"]}
    elif args.stage == "maturity":
        result = maturity.prepare(
            root,
            args.assessment or root / maturity.FOLDER / "spot-check-assessment.json",
            args.primary_documents,
            args.correction_review,
        )
    else:
        from pipeline.release_enrichment import context

        check(root, "maturity", local=True)
        result = context.prepare(root, args.primary_documents or root / context.FOLDER / "primary")
    print(json.dumps({k: v for k, v in result.items() if k not in {"implementation", "artifacts"}}))


if __name__ == "__main__":
    main()
