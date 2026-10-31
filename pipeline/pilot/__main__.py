"""Filter normalized source records to pilot commodities or validate the receipt."""

import argparse
import json
from pathlib import Path

from pipeline.pilot.core import check, filter_pilot


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--check", action="store_true")
    parser.add_argument(
        "--raw", action="store_true", help="Verify local source and pilot bytes too"
    )
    args = parser.parse_args()
    if args.raw and not args.check:
        parser.error("--raw requires --check")
    receipt = check(args.root.resolve(), raw=args.raw) if args.check else filter_pilot(args.root)
    print(
        json.dumps(
            {key: receipt[key] for key in ("status", "input_rows", "kept_rows", "excluded_rows")}
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
