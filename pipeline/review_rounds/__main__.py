import argparse
import json
from pathlib import Path

from .core import check, prepare

parser = argparse.ArgumentParser()
parser.add_argument("--root", type=Path, default=Path.cwd())
parser.add_argument("--development", type=Path)
parser.add_argument("--holdout", type=Path)
parser.add_argument("--check", action="store_true")
parser.add_argument("--local", action="store_true")
args = parser.parse_args()
if not args.check and bool(args.development) != bool(args.holdout):
    parser.error("Provide both completed exports")
result = (
    check(args.root, local=args.local)
    if args.check
    else prepare(args.root, args.development, args.holdout)
)
print(json.dumps(result["review"]))
