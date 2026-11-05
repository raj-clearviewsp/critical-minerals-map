"""Record final-47 chat approval, or check the immutable acceptance receipt."""

import argparse
import json
from pathlib import Path

from pipeline.final_acceptance.core import check, prepare


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--approval", type=Path)
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--local", action="store_true")
    args = parser.parse_args()
    if (args.local and not args.check) or (args.check and args.approval):
        parser.error("Use --local with --check; --approval only for acceptance")
    root = args.root.resolve()
    result = check(root, local=args.local) if args.check else prepare(root, args.approval)
    print(json.dumps({key: result[key] for key in ("status", "approved_on", "review")}))


if __name__ == "__main__":
    main()
