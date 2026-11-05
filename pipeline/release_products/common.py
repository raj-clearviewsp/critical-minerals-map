from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from pipeline.scaffold import write_preserving

TAG = "2026.11.04-v0"
BASE = f"data/releases/{TAG}"
IDENTITY = "reports/phase0-step06-reviewed-audit.json"
MATURITY = "reports/phase0-step07-maturity-audit.json"
CONTEXT = "reports/phase0-step08-context-audit.json"
QA = "reports/phase0-step09-qa-audit.json"
PRODUCTS = "reports/phase0-step10-products-audit.json"
CANONICAL = (
    "source",
    "source_record",
    "site",
    "site_member",
    "facility",
    "production",
    "country_commodity",
    "export_restriction",
    "china_official_finance",
)


def required_inputs(root, stage):
    if stage == QA:
        parents = [
            IDENTITY,
            MATURITY,
            CONTEXT,
            "reports/normalization-audit.json",
            "sources/boundaries/manifest.json",
            "sources/boundaries/countries-50m.geojson.gz",
            *[str(p.relative_to(root)) for p in sorted((root / "sources").glob("*.yaml"))],
        ]
        code = ["common", "geography", "quality"]
    elif stage == PRODUCTS:
        parents = [
            QA,
            "schemas/release-page.schema.json",
            "overrides/pilot_operator_evidence.json",
            "overrides/pilot_operator_documents.json",
            "reports/pilot-audit.json",
            "reports/normalization-audit.json",
        ]
        code = ["common", "catalog", "quality", "downloads", "products", "canonical"]
    else:
        raise ValueError("Unknown release step")
    return parents, ["pipeline/release_products/" + s + ".py" for s in code] + [
        "tools/release/uv.lock"
    ]


def encoded(value):
    return (
        json.dumps(
            value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":")
        )
        + "\n"
    ).encode()


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def metadata(raw):
    return {"bytes": len(raw), "sha256": sha(raw)}


def load(root, path):
    return json.loads((root / path).read_bytes())


def bound(root, receipt_path, name):
    receipt = load(root, receipt_path)
    raw = (root / name).read_bytes()
    if receipt["artifacts"].get(name) != metadata(raw):
        raise ValueError(f"Input differs from its accepted receipt: {name}")
    return json.loads(raw)


def seal(root: Path, receipt_path, outputs, summary, parents, code):
    receipt = {
        "release": TAG,
        **summary,
        "parents": {p: sha((root / p).read_bytes()) for p in parents},
        "implementation": {p: sha((root / p).read_bytes()) for p in code},
        "artifacts": {p: metadata(raw) for p, raw in sorted(outputs.items())},
    }
    validate(root, receipt_path, receipt)
    receipt_bytes = encoded(receipt)
    write_preserving(
        root,
        {
            **outputs,
            receipt_path: receipt_bytes,
            receipt_path + ".sha256": (sha(receipt_bytes) + "\n").encode(),
        },
    )
    return receipt


def validate(root, receipt_path, receipt):
    if receipt.get("release") != TAG or receipt.get("status") != "passed":
        raise ValueError("Invalid release receipt")
    parents, code = required_inputs(root, receipt_path)
    if set(receipt.get("parents", {})) != set(parents) or set(
        receipt.get("implementation", {})
    ) != set(code):
        raise ValueError("Incomplete release input bindings")
    artifacts = receipt.get("artifacts", {})
    counts = receipt.get("counts", {})
    if not artifacts or any(
        set(v) != {"sha256", "bytes"}
        or type(v["bytes"]) is not int
        or v["bytes"] < 1
        or not re.fullmatch("[0-9a-f]{64}", str(v["sha256"]))
        for v in artifacts.values()
    ):
        raise ValueError("Invalid artifact manifest")
    if receipt_path == QA:
        if set(artifacts) != {f"{BASE}/qa/dispositions.json", "reports/phase0-step09-qa.md"}:
            raise ValueError("Incomplete QA artifacts")
        expected = load(root, IDENTITY)["counts"]
        m = load(root, MATURITY)
        if (
            counts.get("sites") != expected["site_entries"]
            or counts.get("observations") != 10788
            or counts.get("site_rungs") != {str(k): v for k, v in m["reviewed_rung_counts"].items()}
            or sum(counts.get("geography", {}).values()) != counts["sites"]
            or type(counts.get("distributable_sites")) is not int
            or not 0 <= counts["distributable_sites"] <= counts["sites"]
        ):
            raise ValueError("QA counts do not reconcile to reviewed inputs")
    else:
        qc = load(root, QA)["counts"]
        pc = counts.get("page_counts", {})
        tc = counts.get("table_counts", {})
        canonical = {f"data/canonical/{TAG}/{n}.parquet" for n in CANONICAL}
        if (
            set(tc) != set(CANONICAL)
            or tc["site"] != qc["distributable_sites"]
            or pc.get("minerals") != 3
            or pc.get("sites")
            != sum(qc["geography"].get(k, 0) for k in ("inside_country", "within_25_km"))
            or not canonical <= artifacts.keys()
            or not {
                "reports/phase0-step10-products.md",
                f"{BASE}/products/atlas.json",
                f"{BASE}/products/downloads/atlas.pmtiles",
                f"data/publish/{TAG}/manifest.json",
            }
            <= artifacts.keys()
            or not 0 < counts.get("pmtiles_bytes", 0) < 50_000_000
        ):
            raise ValueError("Incomplete or inconsistent release products")
        for group in ("sites", "countries", "minerals"):
            if sum(p.startswith(f"{BASE}/products/pages/{group}/") for p in artifacts) != pc[group]:
                raise ValueError("Incomplete page data")


def verify(root, receipt_path, *, local):
    if (
        sha((root / receipt_path).read_bytes())
        != (root / (receipt_path + ".sha256")).read_text().strip()
    ):
        raise ValueError("Release receipt differs from its commitment")
    receipt = load(root, receipt_path)
    validate(root, receipt_path, receipt)
    for k in ("parents", "implementation"):
        for p, h in receipt[k].items():
            if sha((root / p).read_bytes()) != h:
                raise ValueError(f"Changed {k} input: {p}")
    for p, m in receipt["artifacts"].items():
        path = Path(p)
        if (
            path.is_absolute()
            or ".." in path.parts
            or not (
                p.startswith(BASE + "/")
                or p.startswith("reports/")
                or (
                    receipt_path == PRODUCTS
                    and (
                        p.startswith(f"data/canonical/{TAG}/")
                        or p.startswith(f"data/publish/{TAG}/")
                    )
                )
            )
        ):
            raise ValueError("Unexpected release artifact path")
        if local or p.startswith("reports/"):
            if metadata((root / p).read_bytes()) != m:
                raise ValueError(f"Changed release artifact: {p}")
    return receipt
