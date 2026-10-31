"""Build or verify source-aware triage, or validate the separate 20-case review export."""

import argparse
import json
from pathlib import Path

from pipeline.triage.core import check, prepare
from pipeline.triage.labels import load_bound_sample, validate_pilot_labels


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument(
        "--decisions", type=Path, help="Original 200-row CSV with partial human review"
    )
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--raw", action="store_true")
    parser.add_argument("--labels", type=Path, help="New 20-case pilot CSV export")
    parser.add_argument(
        "--complete", action="store_true", help="Require all 20 pilot cases reviewed"
    )
    args = parser.parse_args()
    if args.raw and not args.check:
        parser.error("--raw requires --check")
    if args.complete and not args.labels:
        parser.error("--complete requires --labels")
    if args.labels and (args.check or args.decisions):
        parser.error("--labels cannot combine with --check or --decisions")
    if args.check and args.decisions:
        parser.error("--check verifies the preserved import; do not supply --decisions")
    root = args.root.resolve()
    if args.labels:
        receipt = check(root)
        result = validate_pilot_labels(
            args.labels, load_bound_sample(root, receipt), require_complete=args.complete
        )
        print(json.dumps({key: value for key, value in result.items() if key != "rows"}))
    else:
        receipt = check(root, raw=args.raw) if args.check else prepare(root, args.decisions)
        print(
            json.dumps(
                {
                    key: receipt[key]
                    for key in (
                        "status",
                        "candidate_pairs",
                        "queue_counts",
                        "selection",
                        "workload",
                    )
                }
            )
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
