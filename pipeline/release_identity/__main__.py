"""Produce the private Phase 0 identity release or verify its receipt."""

import argparse
from pathlib import Path

from pipeline.release_identity.core import check, write

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--root", type=Path, default=Path.cwd())
parser.add_argument("--check", action="store_true")
parser.add_argument("--local", action="store_true")
args = parser.parse_args()
result = check(args.root, local=args.local) if args.check else write(args.root)
print(result["status"], result["counts"]["site_entries"], "site entries")
