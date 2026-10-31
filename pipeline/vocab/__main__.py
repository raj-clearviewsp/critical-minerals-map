"""Build or validate the Step 3 vocabulary coverage audit."""

import argparse
import json
from pathlib import Path

from pipeline.vocab.audit import audit, check


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    receipt = (check if args.check else audit)(args.root.resolve())
    print(
        json.dumps(
            {
                "status": receipt["status"],
                "pilot_unmapped": receipt["pilot_unmapped"],
                "sources": len(receipt["sources"]),
                "normalized_rows": 0,
            }
        )
    )
    return 0 if receipt["status"] == "pass" else 2


if __name__ == "__main__":
    raise SystemExit(main())
