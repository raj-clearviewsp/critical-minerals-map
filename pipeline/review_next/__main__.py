"""Prepare the next 27 researched and 33 blind comparisons, or verify their exports."""

import argparse
import json
from pathlib import Path

from pipeline.review_next.core import check, prepare, verify_labels


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--research", type=Path)
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--local", action="store_true")
    parser.add_argument("--labels", type=Path)
    parser.add_argument("--packet", choices=("development", "holdout"))
    parser.add_argument("--complete", action="store_true")
    args = parser.parse_args()
    if (args.local and not args.check) or (args.complete and not args.labels):
        parser.error("--local requires --check; --complete requires --labels")
    if bool(args.labels) != bool(args.packet):
        parser.error("Use --labels and --packet together")
    if sum([bool(args.research), args.check, bool(args.labels)]) > 1:
        parser.error("Choose preparation, artifact verification or export verification")
    root = args.root.resolve()
    if args.labels:
        result = verify_labels(root, args.labels, args.packet, complete=args.complete)
    else:
        receipt = check(root, local=args.local) if args.check else prepare(root, args.research)
        result = {key: receipt[key] for key in ("status", "packets", "research")}
    print(json.dumps(result))


if __name__ == "__main__":
    main()
