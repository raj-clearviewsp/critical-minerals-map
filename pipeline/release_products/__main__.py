import argparse
from pathlib import Path

from .common import PRODUCTS, QA, verify

p = argparse.ArgumentParser(description="Build or verify versioned release data")
p.add_argument("stage", choices=["qa", "products"])
p.add_argument("--root", type=Path, default=Path.cwd())
p.add_argument("--check", action="store_true")
p.add_argument("--local", action="store_true")
p.add_argument("--tippecanoe", type=Path)
a = p.parse_args()
root = a.root.resolve()
if a.check:
    receipt = verify(root, QA if a.stage == "qa" else PRODUCTS, local=a.local)
elif a.stage == "qa":
    from .quality import write

    receipt = write(root)
else:
    from .products import write

    receipt = write(
        root, a.tippecanoe or root / ".cache/release-tools/tippecanoe-2.79.0/tippecanoe"
    )
print(receipt["status"], receipt["counts"])
