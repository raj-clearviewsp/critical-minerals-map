"""Prepare or verify the private Atlas preview and preserved adjudication."""

import argparse
import json
from pathlib import Path

from pipeline.preview.core import check, prepare


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--adjudication", type=Path)
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--local", action="store_true")
    args = parser.parse_args()
    if (args.local and not args.check) or (args.check and args.adjudication):
        parser.error("--local requires --check; verification cannot accept new inputs")
    receipt = (
        check(args.root.resolve(), local=args.local)
        if args.check
        else prepare(args.root.resolve(), args.adjudication)
    )
    print(json.dumps({key: receipt[key] for key in ("status", "review", "dataset")}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
