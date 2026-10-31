"""Prepare or verify a local human-review packet, without fitting or merging."""

import argparse
import json
from pathlib import Path

from pipeline.link.gold_review import DIRECTORY, check, prepare, sample_digest
from pipeline.link.labels import validate_labels
from pipeline.normalize.core import sha256


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--raw", action="store_true")
    parser.add_argument("--labels", type=Path)
    parser.add_argument("--partial", action="store_true")
    args = parser.parse_args()
    root = args.root.resolve()
    if args.raw and not args.check:
        parser.error("--raw requires --check")
    if args.partial and not args.labels:
        parser.error("--partial requires --labels")
    if args.labels and (args.check or args.raw):
        parser.error("--labels cannot be combined with --check/--raw")
    if args.labels:
        receipt = check(root)
        sample_path = root / DIRECTORY / "sample.json"
        sample = json.loads(sample_path.read_text())
        if (
            sample_digest(sample) != receipt["sample_sha256"]
            or sample.get("sample_sha256") != receipt["sample_sha256"]
            or sha256(sample_path) != receipt["artifacts"][f"{DIRECTORY}/sample.json"]["sha256"]
        ):
            parser.error("Local review sample changed")
        result = validate_labels(args.labels, sample, require_complete=not args.partial)
        print(
            json.dumps(
                {
                    key: value
                    for key, value in result.items()
                    if key not in {"rows", "unresolved_pair_ids"}
                }
            )
        )
    else:
        receipt = check(root, raw=args.raw) if args.check else prepare(root)
        print(
            json.dumps(
                {
                    key: receipt[key]
                    for key in (
                        "status",
                        "eligible_rows",
                        "candidate_pairs",
                        "sample_size",
                        "human_labels",
                    )
                }
            )
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
