"""Stable ULIDs and an appendable record-to-site registry with explicit retired aliases."""

from __future__ import annotations

import hashlib
import re
from datetime import UTC, date, datetime, time

import pyarrow as pa
import pyarrow.parquet as pq

ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
REGISTRY_SCHEMA = pa.schema(
    [(k, pa.string()) for k in ("record_id", "site_id", "current_site_id", "first_release")]
)


def mint(record_id: str, release: str) -> str:
    day = date.fromisoformat(release)
    millis = int(datetime.combine(day, time(), UTC).timestamp() * 1000)
    seed = hashlib.sha256(("atlas-site-v0\0" + record_id).encode()).digest()[:10]
    number = (millis << 80) + int.from_bytes(seed, "big")
    return "".join(ALPHABET[(number >> shift) & 31] for shift in range(125, -1, -5))


def allocate(
    groups: list[list[str]], previous: list[dict], release: str
) -> tuple[dict, list[dict]]:
    """Keep birth IDs forever; a reviewed union keeps the oldest ULID. Splits need adjudication."""
    prior = {r["record_id"]: dict(r) for r in previous}
    if len(prior) != len(previous):
        raise ValueError("Duplicate registry record")
    for row in prior.values():
        if not all(
            re.fullmatch(r"[0-7][0-9A-HJKMNP-TV-Z]{25}", row[k])
            for k in ("site_id", "current_site_id")
        ):
            raise ValueError("Invalid existing ULID")
        if date.fromisoformat(row["first_release"]) > date.fromisoformat(release):
            raise ValueError("Registry has IDs from a later release")
    aliases = {}
    for row in prior.values():
        if aliases.setdefault(row["site_id"], row["current_site_id"]) != row["current_site_id"]:
            raise ValueError("Conflicting prior ID aliases")
    known = {v for r in prior.values() for v in (r["site_id"], r["current_site_id"])}
    consumed, assigned, retired = {}, {}, {}
    for members in sorted(tuple(sorted(group)) for group in groups):
        if not members or len(set(members)) != len(members) or any(r in assigned for r in members):
            raise ValueError("Site membership must be disjoint and nonempty")
        old = {prior[r]["current_site_id"] for r in members if r in prior}
        if any(k in consumed for k in old):
            raise ValueError("Existing site would split; explicit adjudication required")
        if old:
            site = min(old)
            retired.update({k: site for k in old if k != site})
            consumed.update(dict.fromkeys(old, site))
        else:
            site = mint(members[0], release)
            if site in known:
                raise ValueError("ULID collision or attempted ID reuse")
            known.add(site)
        for record in members:
            assigned[record] = site
            prior.setdefault(
                record,
                {
                    "record_id": record,
                    "site_id": site,
                    "current_site_id": site,
                    "first_release": release,
                },
            )
    for row in prior.values():
        row["current_site_id"] = retired.get(row["current_site_id"], row["current_site_id"])
    return assigned, [prior[k] for k in sorted(prior)]


def parquet_bytes(rows: list[dict]) -> bytes:
    buf = pa.BufferOutputStream()
    pq.write_table(pa.Table.from_pylist(rows, schema=REGISTRY_SCHEMA), buf, compression="zstd")
    return buf.getvalue().to_pybytes()


def read_registry(payload):
    table = pq.read_table(pa.BufferReader(payload))
    if table.schema.remove_metadata() != REGISTRY_SCHEMA or any(
        table[k].null_count for k in table.column_names
    ):
        raise ValueError("Unrecognized persistent ID registry")
    return table.to_pylist()


def preserves_ids(snapshot, current):
    old = read_registry(snapshot)
    live = read_registry(current)
    rows = {r["record_id"]: r for r in live}
    if len(rows) != len(live):
        return False
    births = {r["site_id"] for r in live}
    if any(r["current_site_id"] not in births or r["current_site_id"] > r["site_id"] for r in live):
        return False
    current_ids = {}
    for r in old:
        if r["record_id"] not in rows:
            return False
        live_id = rows[r["record_id"]]["current_site_id"]
        if (
            live_id > r["current_site_id"]
            or current_ids.setdefault(r["current_site_id"], live_id) != live_id
        ):
            return False
    return all(
        r["record_id"] in rows
        and all(rows[r["record_id"]][k] == r[k] for k in ("site_id", "first_release"))
        for r in old
    )
