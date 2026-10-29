"""Fetch one registered source or verify an existing immutable snapshot."""

import argparse
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

from pipeline.fetch.core import FetchError, PoliteHTTP, fetch_source, load_source, verify_snapshot
from pipeline.scaffold import json_bytes


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--source")
    group.add_argument("--list", action="store_true")
    group.add_argument("--verify", type=Path)
    parser.add_argument(
        "--refresh", action="store_true", help="Recheck remote bytes; never overwrite raw"
    )
    args = parser.parse_args()
    root = args.root.resolve()
    http = None
    result = {}
    try:
        if args.list:
            sources = [
                load_source(root, path.stem) for path in sorted((root / "sources").glob("*.yaml"))
            ]
            print(
                json.dumps(
                    [
                        {
                            key: source[key]
                            for key in (
                                "source_id",
                                "license_status",
                                "access_status",
                                "redistribution_ok",
                            )
                        }
                        for source in sources
                    ],
                    indent=2,
                )
            )
            return 0
        if args.verify:
            value = verify_snapshot(args.verify)
            print(
                json.dumps(
                    {
                        "status": "verified",
                        "source_id": value["source_id"],
                        "files": len(value["files"]),
                    }
                )
            )
            return 0
        http = PoliteHTTP(os.environ.get("ATLAS_CONTACT", ""))
        result = fetch_source(root, args.source, http, refresh=args.refresh)
        manifest = result["manifest"]
        summary = {
            "source_id": args.source,
            "status": result["status"],
            "snapshot": result["snapshot"],
            "file_count": len(manifest["files"]),
            "bytes": sum(row["bytes"] for row in manifest["files"]),
            "row_count": None,
            "note": "Raw capture only; row counts await Step 4 normalization.",
        }
        print(json.dumps(summary))
    except FetchError as error:
        summary = {
            "source_id": args.source,
            "status": "blocked",
            "reason": str(error),
            "row_count": None,
        }
        print(json.dumps(summary), file=sys.stderr)
    finally:
        if http:
            http.close()
    reports = root / "reports/fetch"
    reports.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S.%fZ")
    (reports / f"{args.source}-{stamp}.json").write_bytes(json_bytes(summary))
    return 0 if result else 2


if __name__ == "__main__":
    sys.exit(main())
