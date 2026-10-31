"""Fetch a registered source, import original local downloads or verify a snapshot."""

import argparse
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

from pipeline.fetch.core import FetchError, PoliteHTTP, fetch_source, load_source, verify_snapshot
from pipeline.fetch.local import import_local, load_intake
from pipeline.scaffold import json_bytes


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--source")
    group.add_argument("--list", action="store_true")
    group.add_argument("--verify", type=Path)
    group.add_argument("--import-local", type=Path, metavar="INTAKE_JSON_OR_YAML")
    parser.add_argument(
        "--refresh", action="store_true", help="Recheck remote bytes; never overwrite raw"
    )
    args = parser.parse_args()
    root = args.root.resolve()
    http = None
    result = {}
    source_id = args.source
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
        if args.import_local:
            if args.refresh:
                raise FetchError("--refresh only applies to remote source downloads")
            intake = load_intake(args.import_local.resolve())
            source_id = intake["source_id"]
            result = import_local(root, intake)
        else:
            http = PoliteHTTP(os.environ.get("ATLAS_CONTACT", ""))
            result = fetch_source(root, source_id, http, refresh=args.refresh)
        manifest = result["manifest"]
        summary = {
            "source_id": source_id,
            "status": result["status"],
            "snapshot": result["snapshot"],
            "file_count": len(manifest["files"]),
            "bytes": sum(row["bytes"] for row in manifest["files"]),
            "row_count": None,
            "note": "Raw capture only; row counts await Step 4 normalization.",
        }
        for key in ("registry_metadata_changed", "current_registry_sha256"):
            if key in result:
                summary[key] = result[key]
        print(json.dumps(summary))
    except FetchError as error:
        summary = {
            "source_id": source_id,
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
    (reports / f"{source_id or 'intake'}-{stamp}.json").write_bytes(json_bytes(summary))
    return 0 if result else 2


if __name__ == "__main__":
    sys.exit(main())
