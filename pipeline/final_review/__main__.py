import argparse
import json
from pathlib import Path

from .core import check, prepare, verify_labels

parser = argparse.ArgumentParser()
parser.add_argument("--root", type=Path, default=Path.cwd())
parser.add_argument("--research", type=Path)
parser.add_argument("--check", action="store_true")
parser.add_argument("--local", action="store_true")
parser.add_argument("--labels", type=Path)
parser.add_argument("--complete", action="store_true")
args = parser.parse_args()
if args.labels:
    result = verify_labels(args.root, args.labels, complete=args.complete)
else:
    receipt = (
        check(args.root, local=args.local) if args.check else prepare(args.root, args.research)
    )
    result = {k: receipt[k] for k in ("status", "research", "sample_sha256")}
print(json.dumps(result))
