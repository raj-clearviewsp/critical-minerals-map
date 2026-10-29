"""Run one explicit pipeline stage; never fetch data implicitly."""

import argparse
import json
import sys
from pathlib import Path

from pipeline.scaffold import STAGES, ScaffoldError, run_stage


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--release-tag", default="2026.10.28")
    parser.add_argument("stage", choices=STAGES)
    args = parser.parse_args()
    try:
        report = run_stage(args.root, args.stage, args.release_tag)
    except ScaffoldError as error:
        print(f"Atlas stopped: {error}", file=sys.stderr)
        return 2
    print(json.dumps({key: report[key] for key in ("stage", "status", "rows", "rejects")}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
