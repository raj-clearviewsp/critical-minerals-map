"""Normalize verified source snapshots or verify a completed staging receipt."""

import argparse
import json
from pathlib import Path

from pipeline.normalize.core import check, normalize


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--raw", action="store_true", help="Verify local raw and staging bytes too")
    args = parser.parse_args()
    receipt = check(args.root.resolve(), raw=args.raw) if args.check else normalize(args.root)
    print(json.dumps({k: receipt[k] for k in ("status", "rows", "rejects")}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
