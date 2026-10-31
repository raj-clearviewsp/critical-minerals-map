"""Explicit review of REE-compilation rows excluded by their commodity cells."""

import json

import pyarrow.parquet as pq

from pipeline.pilot.geology import commodity_evidence


def records(root, vocab):
    """Retain contradictory source scope/mineralogy for a human decision, not inference."""
    result = []
    path = root / "data/staging/usgs_global_ree.parquet"
    for batch in pq.ParquetFile(path).iter_batches(batch_size=1000):
        for row in batch.to_pylist():
            evidence = commodity_evidence("usgs_global_ree", row, vocab)
            if any(set(item["codes"]) & vocab.pilot_codes for item in evidence):
                continue
            raw = json.loads(row["raw_fields"])
            result.append(
                {
                    key: row[key]
                    for key in (
                        "record_id",
                        "source_id",
                        "raw_name",
                        "country",
                        "source_file",
                        "source_table",
                        "source_row",
                        "source_url",
                        "raw_sha256",
                    )
                }
                | {
                    "raw_commodities": row["raw_commodities"],
                    "ree_minerals": raw.get("REE_Mins"),
                    "reason": "Explicit Commods lists no reviewed pilot commodity; REE compilation "
                    "scope and mineralogy warrant review, but do not override the nonblank "
                    "commodity cell automatically.",
                    "decision": "",
                    "reviewer": "",
                }
            )
    return result
