"""Import the completed review, prepare evidence or verify separate validation exports."""

import argparse
import json
from pathlib import Path

from pipeline.reconciliation.core import PACKET_MODES, check, load_packet, prepare, validate_export


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--decisions", type=Path)
    parser.add_argument("--evidence", type=Path)
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--local", action="store_true")
    parser.add_argument("--labels", type=Path)
    parser.add_argument("--packet", choices=PACKET_MODES)
    parser.add_argument("--complete", action="store_true")
    args = parser.parse_args()
    if (args.local and not args.check) or (args.complete and not args.labels):
        parser.error("--local requires --check; --complete requires --labels")
    if bool(args.labels) != bool(args.packet):
        parser.error("--labels and --packet must be supplied together")
    if (args.check or args.labels) and (args.decisions or args.evidence):
        parser.error("Verification cannot combine with new input files")
    if args.check and args.labels:
        parser.error("Choose artifact verification or label verification")
    root = args.root.resolve()
    if args.labels:
        receipt = check(root)
        result = validate_export(
            args.labels, load_packet(root, receipt, args.packet), complete=args.complete
        )
    else:
        receipt = (
            check(root, local=args.local)
            if args.check
            else prepare(root, args.decisions, args.evidence)
        )
        result = {
            key: receipt[key] for key in ("status", "research", "packets", "historical_readiness")
        }
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
