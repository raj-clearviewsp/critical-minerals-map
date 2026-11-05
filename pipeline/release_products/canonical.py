"""Private, versioned canonical tables, independently of distribution eligibility."""

from __future__ import annotations

import hashlib
import json
import math
import stat
import struct
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import yaml
from pyproj import CRS

from .catalog import ops, verified_citation
from .common import BASE, CONTEXT, IDENTITY, MATURITY, QA, TAG, bound, load, sha
from .downloads import TABLES, arrow_type, canonical
from .production_evidence import reviewed_for_release
from .quality import FILE as QA_DISPOSITIONS
from .quality import source_registry

PREFIX = f"data/canonical/{TAG}"
PILOT = "reports/pilot-audit.json"
NORMALIZATION = "reports/normalization-audit.json"
EXTRA = {
    "source": "license_status registered_at source_manifest_sha256 source_metadata",
    "site": (
        "alt_names primary_commodities byproduct_commodities admin1 "
        "location_precision_m national_system original_maturity_rung maturity_label "
        "maturity_as_of maturity_evidence status_evidence reporting_evidence "
        "status_observations correction limitations geography geographic_pass "
        "distributable download_holds site_provenance identity_decisions reviewed_maturity_rung "
        "reviewed_status reviewed_status_as_of reviewed_rung_evidence reviewed_status_evidence "
        "publication_correction publication_source_checks"
    ),
    "site_member": "match_score identity_decisions",
    "facility": (
        "country_iso3 location_precision capacity capacity_unit operator_evidence "
        "distributable download_holds geography publication_correction"
    ),
    "production": "scope production_qualifier",
    "country_commodity": "production_evidence reserves_evidence source_payload",
    "export_restriction": (
        "commodities source_area product measure_code descriptions source_payload"
    ),
    "china_official_finance": "source_payload",
}
LIST = {
    "alt_names",
    "primary_commodities",
    "byproduct_commodities",
    "download_holds",
    "identity_decisions",
    "limitations",
}
BOOL = {"national_system", "geographic_pass", "distributable"}
FLOAT = {"match_score", "capacity", "location_precision_m"}
STRUCT = {
    "evidence",
    "source_metadata",
    "site_provenance",
    "maturity_evidence",
    "status_evidence",
    "reviewed_rung_evidence",
    "reviewed_status_evidence",
    "publication_correction",
    "publication_source_checks",
    "reporting_evidence",
    "status_observations",
    "operator_evidence",
    "correction",
    "source_payload",
    "production_evidence",
    "reserves_evidence",
    "descriptions",
}


def _metadata(spatial):
    meta = {b"atlas_canonical_release": TAG.encode(), b"atlas_visibility": b"private"}
    if spatial:
        point = {
            "encoding": "WKB",
            "geometry_types": ["Point"],
            "crs": CRS.from_user_input("OGC:CRS84").to_json_dict(),
        }
        meta[b"geo"] = canonical(
            {"version": "1.1.0", "primary_column": "geometry", "columns": {"geometry": point}}
        ).encode()
    return meta


def _point(x, y, precision):
    if x is None and y is None:
        return None
    if (
        x is None
        or y is None
        or not math.isfinite(x)
        or not math.isfinite(y)
        or not -180 <= x <= 180
        or not -90 <= y <= 90
    ):
        raise ValueError("Invalid retained source coordinates")
    if precision in {"none", "country_centroid", "country centroid"}:
        return None
    return struct.pack("<BIdd", 1, 1, x, y)


def _dtype(col):
    if col in LIST:
        return pa.list_(pa.field("element", pa.string()))
    if col in BOOL:
        return pa.bool_()
    if col in FLOAT:
        return pa.float64()
    if col in {"original_maturity_rung", "reviewed_maturity_rung"}:
        return pa.int64()
    return arrow_type(col)


def _parquet(table, rows, *, spatial=False):
    columns = [*TABLES[table], *EXTRA.get(table, "").split()]
    schema = pa.schema(
        [(c, _dtype(c)) for c in columns] + ([("geometry", pa.binary())] if spatial else []),
        metadata=_metadata(spatial),
    )
    flattened = []
    for row in rows:
        if set(row) - set(columns):
            raise ValueError(f"Unexpected private {table} fields: {set(row) - set(columns)}")
        item = {c: (canonical(v) if c in STRUCT and v is not None else v) for c, v in row.items()}
        if spatial:
            item["geometry"] = _point(item["lon"], item["lat"], item.get("location_precision"))
        flattened.append(item)
    t = pa.Table.from_pylist(flattened, schema=schema)
    out = pa.BufferOutputStream()
    pq.write_table(t, out, compression="zstd", version="2.6")
    data = out.getvalue().to_pybytes()
    if not pq.read_table(pa.BufferReader(data)).equals(t, check_metadata=True):
        raise ValueError(f"Canonical {table} failed round trip")
    return data


def _file_hash(path):
    with path.open("rb") as f:
        return hashlib.file_digest(f, "sha256").hexdigest()


def _pilot_sources(root, pilot):
    sources = {}
    expected = len(pilot["sources"])
    for s in pilot["sources"]:
        sid = s["source_id"]
        rel = f"data/staging/pilot/{sid}.parquet"
        if s["artifact"] != rel or not sid or "/" in sid or ".." in sid or sid in sources:
            raise ValueError("Invalid pilot source inventory")
        path = root / rel
        if not stat.S_ISREG(path.stat().st_mode) or _file_hash(path) != s["sha256"]:
            raise ValueError(f"Pilot artifact changed: {sid}")
        sources[sid] = (path, s["kept_rows"])
    if len(sources) != expected or sum(n for _, n in sources.values()) != pilot["kept_rows"]:
        raise ValueError("Pilot count reconciliation failed")
    return sources


def _source_records(root, pilot, observations, members):
    """Write row groups incrementally and compare every native column to its original batch."""
    sources = _pilot_sources(root, pilot)
    first = pq.read_schema(next(iter(sources.values()))[0])
    out = pa.BufferOutputStream()
    seen = {}
    roles = {}
    batches = 0
    if first.metadata.get(b"atlas_schema") != b"pilot-source-record-v1":
        raise ValueError("Not a pilot source-record contract")
    extras = [
        ("site_id", pa.string()),
        ("identity_group_id", pa.string()),
        ("identity_review_state", pa.string()),
        ("physical_eligibility", pa.string()),
        ("geometry", pa.binary()),
    ]
    scheme = pa.schema(
        [*first, *[pa.field(k, t) for k, t in extras]],
        metadata={**first.metadata, **_metadata(True)},
    )
    with pq.ParquetWriter(out, scheme, compression="zstd", version="2.6") as writer:
        for sid, (path, n) in sorted(sources.items()):
            source = pq.ParquetFile(path)
            if (
                not source.schema_arrow.equals(first, check_metadata=True)
                or source.metadata.num_rows != n
            ):
                raise ValueError("Pilot schema or row count changed")
            for batch in source.iter_batches(batch_size=1024):
                table = pa.Table.from_batches([batch])
                keys = table["record_id"].to_pylist()
                sids = table["source_id"].to_pylist()
                if (
                    any(not k or k in seen for k in keys)
                    or len(set(keys)) != len(keys)
                    or set(sids) != {sid}
                ):
                    raise ValueError("Duplicate source record or invalid source ID")
                seen.update(dict.fromkeys(keys, sid))
                located = [observations.get(k, {}) for k in keys]
                coordinates = [
                    _point(x, y, v)
                    for x, y, v in zip(
                        table["lon"].to_pylist(),
                        table["lat"].to_pylist(),
                        table["location_precision"].to_pylist(),
                        strict=True,
                    )
                ]
                columns = [
                    [members.get(k) for k in keys],
                    [r.get("identity_group_id") for r in located],
                    [r.get("review_state") for r in located],
                    [r.get("physical_eligibility") for r in located],
                    coordinates,
                ]
                for (k, t), col in zip(extras, columns, strict=True):
                    table = table.append_column(k, pa.array(col, type=t))
                writer.write_table(
                    table.replace_schema_metadata(scheme.metadata), row_group_size=1024
                )
                batches += 1
                if set(keys) & members.keys():
                    for k, raw in zip(keys, table["pilot_evidence"].to_pylist(), strict=True):
                        if k in members:
                            roles[k] = [
                                (e["role"], c)
                                for e in json.loads(raw)
                                if not e.get("qualified")
                                for c in e.get("codes", [])
                            ]
    data = out.getvalue().to_pybytes()
    result = pq.ParquetFile(pa.BufferReader(data))
    if (
        len(seen) != pilot["kept_rows"]
        or result.metadata.num_rows != len(seen)
        or result.num_row_groups != batches
        or not observations.keys() <= seen.keys()
    ):
        raise ValueError("Canonical source inventory did not reconcile")
    group = 0
    for path, _ in (sources[sid] for sid in sorted(sources)):
        for batch in pq.ParquetFile(path).iter_batches(batch_size=1024):
            if not result.read_row_group(group, columns=first.names).equals(
                pa.Table.from_batches([batch])
            ):
                raise ValueError("Canonical source record differs from original pilot row")
            group += 1
    return data, seen, roles


def _stage(root, receipt, group, name):
    return bound(root, receipt, f"{BASE}/{group}/{name}.json")


def _facts(e):
    return {
        k: e.get(k)
        for k in ("source_id", "source_url", "retrieved_at", "evidence_grade", "raw_sha256")
    }


def _join_sites(sites, observations, original, reviewed, corrections, dispositions, roles):
    initial = {r["site_id"]: r for r in original}
    grades = {r["site_id"]: r for r in reviewed}
    changes = {r["site_id"]: r for r in corrections}
    qa = {r["site_id"]: r for r in dispositions}
    ids = {s["site_id"] for s in sites}
    if (
        ids != initial.keys()
        or ids != grades.keys()
        or ids != qa.keys()
        or len(ids) != len(sites)
        or len(changes) != len(corrections)
        or set(changes) != {key for key in ids if initial[key] != grades[key]}
    ):
        raise ValueError("Maturity, correction, identity or QA coverage differs")
    result = []
    for s in sites:
        sid = s["site_id"]
        m = grades[sid]
        old = initial[sid]
        c = changes.get(sid)
        d = qa[sid]
        rids = s["record_ids"]
        a = observations[s["coordinate_record_id"]]
        if (
            set(m["source_record_ids"]) != set(rids)
            or s["coordinate_record_id"] not in rids
            or (s["lat"], s["lon"]) != (a["lat"], a["lon"])
        ):
            raise ValueError("Site coordinate or maturity member changed")
        if c and (
            c["original_rung"] != old["maturity_rung"]
            or c["reviewed_rung"] != m["maturity_rung"]
            or not set(c["source_record_ids"]) <= set(rids)
            or not c["evidence"]
        ):
            raise ValueError("Invalid post-assessment correction")

        def role(name, rids=rids):
            return sorted({code for rid in rids for rank, code in roles[rid] if rank == name})

        recordset = [observations[r] for r in rids]
        base = {**a["evidence"], "source_id": a["source_id"], "source_url": a["source_url"]}
        result.append(
            {
                **{
                    k: s[k]
                    for k in (
                        "site_id",
                        "name",
                        "lat",
                        "lon",
                        "coordinate_record_id",
                        "location_precision",
                        "country_iso3",
                        "commodities",
                        "review_state",
                        "record_ids",
                    )
                },
                "alt_names": sorted({x for r in recordset for x in r["aliases"] if x != s["name"]}),
                "primary_commodities": role("primary"),
                "byproduct_commodities": role("byproduct"),
                "deposit_type": ", ".join(
                    sorted({v for r in recordset for v in r["deposit_types"]})
                )
                or None,
                "maturity_rung": m["maturity_rung"],
                "maturity_reason": m["rung_evidence"]["reason"],
                "maturity_label": m["maturity_label"],
                "maturity_as_of": m["as_of"],
                "original_maturity_rung": old["maturity_rung"],
                "status": m["status"],
                "status_as_of": m["status_as_of"],
                "reporting_system": m["reporting_system"],
                "national_system": m["national_system"],
                "maturity_evidence": m["rung_evidence"],
                "status_evidence": m["status_evidence"],
                "status_observations": m["status_observations"],
                "reporting_evidence": m["reporting_evidence"],
                "correction": c,
                "limitations": m["limitations"],
                "site_provenance": s["provenance"],
                "identity_decisions": s["decision_pair_ids"],
                "geography": d["geography"],
                "geographic_pass": d["geographic_pass"],
                "distributable": d["distributable"],
                "download_holds": d["download_holds"],
                **_facts(base),
                "evidence": {"coordinate": base, "member_ids": rids},
            }
        )
    return result


def _publication_sites(sites, baseline, effective, audit):
    """Apply the publication correction only after validating the sealed Step 7 assignments."""
    old = {row["site_id"]: row for row in baseline}
    current = {row["site_id"]: row for row in effective}
    changes = {c["site_id"]: c for c in audit["corrections"]}
    ids = {s["site_id"] for s in sites}
    if (
        ids != old.keys()
        or ids != current.keys()
        or {sid for sid in ids if old[sid] != current[sid]} != changes.keys()
        or len(changes) != len(audit["corrections"])
    ):
        raise ValueError("Publication correction coverage differs from the reviewed sites")
    source_checks = {sid: [] for sid in ids}
    for doc in audit["source_checks"]:
        for binding in doc["bindings"]:
            sid = binding["site_id"]
            if sid is not None:
                source_checks[sid].append(
                    {
                        **{
                            k: doc[k]
                            for k in (
                                "source_id",
                                "url",
                                "sha256",
                                "reporting_period_end",
                                "retrieved_at",
                                "evidence_grade",
                            )
                        },
                        **binding,
                    }
                )
    for site in sites:
        sid = site["site_id"]
        before = old[sid]
        after = current[sid]
        change = changes.get(sid)
        site.update(
            reviewed_maturity_rung=before["maturity_rung"],
            reviewed_status=before["status"],
            reviewed_status_as_of=before["status_as_of"],
            reviewed_rung_evidence=before["rung_evidence"],
            reviewed_status_evidence=before["status_evidence"],
            publication_correction=change,
            publication_source_checks=source_checks[sid],
        )
        if change is None:
            continue
        ev = after["rung_evidence"]
        doc = ev["verified_document"]
        if (
            change["baseline"]["maturity_rung"] != before["maturity_rung"]
            or change["baseline"]["status"] != before["status"]
            or change["baseline_rung_evidence"] != before["rung_evidence"]
            or change["record_id"] not in site["record_ids"]
            or change["effective_rung"] != after["maturity_rung"]
            or change["effective_status"] != after["status"]
            or change["effective_status_as_of"] != after["status_as_of"]
            or change["evidence"] != ev
            or doc["redistribution_ok"] is not False
            or doc["source_id"] != change["operator_source_id"]
        ):
            raise ValueError(
                "Publication correction lacks its exact approved site or verified document"
            )
        site.update(
            maturity_rung=after["maturity_rung"],
            maturity_label=after["maturity_label"],
            maturity_reason=ev["reason"],
            maturity_evidence=ev,
            status=after["status"],
            status_as_of=after["status_as_of"],
            status_evidence=after["status_evidence"],
            status_observations=after["status_observations"],
            distributable=False,
            download_holds=sorted(set(site["download_holds"]) | {change["distribution_hold"]}),
        )
        site["evidence"] = {**site["evidence"], "publication_production": ev}
    return sites


def _context(root, source_ids, site_ids, registry=None):
    groups = {
        n: _stage(root, CONTEXT, "context", f)
        for n, f in [
            ("country_commodity", "country_commodity"),
            ("export_restriction", "export_restrictions"),
            ("china_official_finance", "china_official_finance"),
        ]
    }
    result = {n: [] for n in (*groups, "production")}
    receipt = load(root, CONTEXT)
    if [len(rows) for rows in groups.values()] != [
        receipt["counts"][n]
        for n in ("country_commodity", "export_restrictions", "china_official_finance")
    ]:
        raise ValueError("Private context counts do not reconcile")

    def check(e):
        if e["record_id"] not in source_ids or source_ids[e["record_id"]] != e["source_id"]:
            raise ValueError("Context evidence outside the canonical pilot")

    registered_chapters = {}

    def chapter(ev):
        if not ev:
            return None
        if ev.get("source_id_kind") != "chapter_table_observation":
            check(ev)
            return ev
        staged = ev["staged_observation"]
        check(staged)
        if ev["record_id"] != staged["record_id"]:
            raise ValueError("Chapter changed the underlying observed record")
        doc_id = "document_" + sha(ev["source_url"].encode())[:16]
        descriptor = (ev["source_url"], ev["raw_sha256"])
        if doc_id not in registered_chapters:
            relative = f"{BASE}/context/primary/{ev['source_file']}"
            if (
                Path(ev["source_file"]).name != ev["source_file"]
                or receipt["artifacts"].get(relative, {}).get("sha256") != ev["raw_sha256"]
                or _file_hash(root / relative) != ev["raw_sha256"]
            ):
                raise ValueError("MCS chapter source is not the verified Step8 document")
            registered_chapters[doc_id] = descriptor
        elif registered_chapters[doc_id] != descriptor:
            raise ValueError("Conflicting primary chapter bytes")
        if registry is not None:
            doc = registry.setdefault(
                doc_id,
                {
                    "id": doc_id,
                    "title": f"USGS Mineral Commodity Summaries 2026 · {ev['source_file']}",
                    "publisher": "U.S. Geological Survey",
                    "license": ev["license"],
                    "redistribution_ok": ev["redistribution_ok"],
                    "grade": ev["evidence_grade"],
                    "url": ev["source_url"],
                    "retrieved_at": ev["retrieved_at"],
                },
            )
            if (
                doc["url"] != ev["source_url"]
                or doc.get("checksum", ev["raw_sha256"]) != ev["raw_sha256"]
            ):
                raise ValueError("MCS document registry URL or checksum disagrees with chapter")
            doc["checksum"] = ev["raw_sha256"]
        return {**ev, "source_id": doc_id}

    for row in groups["country_commodity"]:
        p = chapter(row["production_evidence"])
        r = chapter(row["reserves_evidence"])
        fields = [k for k in TABLES["country_commodity"] if k in row]
        result["country_commodity"].append(
            {
                **{k: row[k] for k in fields},
                **_facts(p),
                "production_evidence": p,
                "reserves_evidence": r,
                "source_payload": row,
                "evidence": [p, *([r] if r else [])],
            }
        )
        if row["scope"] == "country":
            result["production"].append(
                {
                    "entity_ref": row["country_iso3"],
                    "entity_type": "country",
                    "scope": "country",
                    "commodity": row["commodity"],
                    "year": row["year"],
                    "quantity": row["production"],
                    "unit": row["unit"],
                    "quantity_basis": row["quantity_basis"],
                    "production_estimated": row["production_estimated"],
                    "production_qualifier": row["production_qualifier"],
                    **_facts(p),
                    "evidence": [p],
                }
            )
    for row in groups["export_restriction"]:
        e = row["source_evidence"]
        check(e)
        for item in row["descriptions"].values():
            if source_ids.get(item["record_id"]) != e["source_id"]:
                raise ValueError("OECD description has no pilot source record")
        result["export_restriction"].append(
            {
                **{
                    k: row[k]
                    for k in (
                        "country_iso3",
                        "year",
                        "measure_type",
                        "source_area",
                        "product",
                        "measure_code",
                        "variation",
                        "start_date_raw",
                        "end_date_raw",
                        "descriptions",
                    )
                },
                "commodities": row["commodity_families"],
                "commodity_code": row["product_code"],
                "value": row["descriptions"].get("VAL", {}).get("value"),
                "unit": row["descriptions"].get("VAL_UNIT", {}).get("value"),
                "value_condition": row["descriptions"].get("VAL_CONDITION", {}).get("value"),
                **_facts(e),
                "source_payload": row,
                "evidence": [
                    e,
                    *[
                        {
                            **_facts(e),
                            "record_id": item["record_id"],
                            "source_row": item["source_row"],
                            "field": field,
                        }
                        for field, item in row["descriptions"].items()
                        if field in ("VAL", "VAL_UNIT", "VAL_CONDITION", "DATI", "END")
                    ],
                ],
            }
        )
    for row in groups["china_official_finance"]:
        e = row["source_evidence"]
        check(e)
        link = row.get("site_id")
        if link is not None and link not in site_ids:
            raise ValueError("Financing link outside canonical site register")
        amount = row.get("amount_constant_usd_2023")
        value = float(amount) if amount is not None else None
        if value is not None and not math.isfinite(value):
            raise ValueError("Nonfinite reported financing amount")
        result["china_official_finance"].append(
            {
                "project_id": str(row["aiddata_record_id"]),
                "country_iso3": row["country_iso3"],
                "title": row["title"],
                "commitment_year": int(row["year"]) if row["year"] is not None else None,
                "amount": value,
                "currency": "USD",
                "amount_basis": "Constant 2023 USD",
                **{
                    k: row.get(k)
                    for k in (
                        "amount_estimated",
                        "recommended_for_aggregates",
                        "source_status",
                        "flow_type",
                    )
                },
                "lender": row["lender"],
                "commodities": row["commodity_families"],
                "site_id": link,
                **_facts(e),
                "evidence": [e],
                "source_payload": row,
            }
        )
    return result


def _sources(root, registry, source_ids, descriptors):
    result = []
    public = {s["source_id"]: s for s in descriptors}
    accepted = {s["source_id"]: s for s in load(root, NORMALIZATION)["sources"]}
    for sid in sorted(source_ids | public.keys()):
        r = registry[sid]
        registry_path = root / "sources" / f"{sid}.yaml"
        native_registry = (
            yaml.safe_load(registry_path.read_bytes()) if registry_path.is_file() else {}
        )
        manifest_hash = r.get("source_manifest_sha256")
        manifest_path = r.get("raw_manifest_path")
        if sid in accepted:
            if (
                not manifest_path
                or manifest_hash != accepted[sid]["manifest_sha256"]
                or _file_hash(root / manifest_path) != manifest_hash
            ):
                raise ValueError("Registry lost the normalization-bound original manifest")
        elif manifest_path or manifest_hash:
            raise ValueError("Unexpected snapshot manifest for independent document")
        p = public.get(sid, {})
        registered = r.get("registered_at")
        checksum = manifest_hash or r.get("checksum") or p.get("checksum")
        basis = ("snapshot_manifest" if manifest_hash else "document_bytes") if checksum else None
        citation_only = (
            not checksum and not p and not manifest_hash and r.get("redistribution_ok") is False
        )
        if not citation_only and (
            not checksum
            or len(checksum) != 64
            or any(c not in "0123456789abcdef" for c in checksum)
        ):
            raise ValueError("Private source needs a bound digest for its declared artifact")
        if p and (
            p["checksum"] != checksum or p.get("checksum_basis") != basis or p["url"] != r["url"]
        ):
            raise ValueError("Private and public source URL or checksum disagrees")
        result.append(
            {
                "source_id": sid,
                "name": r["title"],
                "publisher": r.get("publisher"),
                "url": r["url"],
                "license": r.get("license"),
                "redistribution_ok": r.get("redistribution_ok") is True,
                "currency_date": r.get("currency"),
                "retrieved_at": r.get("retrieved_at")
                if manifest_hash
                else r.get("retrieved_at") or p.get("retrieved_at"),
                "checksum": checksum,
                "attribution": r.get("attribution"),
                "evidence_grade": r.get("grade"),
                "registered_at": registered,
                "source_manifest_sha256": manifest_hash,
                "source_metadata": {
                    **r,
                    "archive_state": "citation_only" if citation_only else "checksum_bound",
                    "registry": {
                        k: native_registry.get(k)
                        for k in (
                            "license_status",
                            "license_evidence_url",
                            "spec_license_claim",
                            "evidence_grade",
                            "currency",
                        )
                    },
                },
                "checksum_basis": basis,
                "license_status": native_registry.get("license_status"),
            }
        )
    return result


def _facility_rows(sites, observations, operators, registry):
    facilities = []
    for s in sites:
        if s["maturity_rung"] != 5:
            continue
        covered = [operators[r] for r in s["record_ids"] if r in operators]
        if len({v[0]["operator"] for v in covered}) > 1:
            raise ValueError("Conflicting operator claims on canonical mine")
        operator, evidence = covered[0] if covered else ({}, None)
        holds = set(s["download_holds"])
        if evidence and not registry.get(evidence["source_id"], {}).get("redistribution_ok"):
            holds.add("operator_source_terms")
        e = s["maturity_evidence"]
        r = e["record_id"]
        if r not in s["record_ids"] or observations[r]["site_id"] != s["site_id"]:
            raise ValueError("Mine evidence crosses site boundary")
        fact_source = _facts(e)
        correction = s.get("publication_correction")
        if correction:
            doc = e["verified_document"]
            fact_source = {
                "source_id": doc["source_id"],
                "source_url": doc["url"],
                "retrieved_at": doc["retrieved_at"],
                "evidence_grade": doc["evidence_grade"],
                "raw_sha256": doc["sha256"],
            }
        facilities.append(
            {
                "facility_id": "mine_" + s["site_id"],
                "site_id": s["site_id"],
                "name": s["name"],
                "facility_type": "mine",
                "lat": s["lat"],
                "lon": s["lon"],
                "location_precision": s["location_precision"],
                "country_iso3": s["country_iso3"],
                "commodities": s["commodities"],
                "status": s["status"],
                "status_as_of": s["status_as_of"],
                "operator_name": operator.get("operator"),
                "operator_evidence": evidence,
                "distributable": not holds,
                "download_holds": sorted(holds),
                "geography": s["geography"],
                **fact_source,
                "publication_correction": correction,
                "evidence": {
                    "operating_mine": e,
                    "coordinate_record_id": s["coordinate_record_id"],
                },
            }
        )
    return facilities


def canonical_files(root: Path, atlas: dict, export_tables: dict) -> dict[str, bytes]:
    """Return private versioned products as bytes. The release owner performs atomic sealing."""
    pilot = load(root, PILOT)
    if pilot["normalization_audit_sha256"] != _file_hash(root / NORMALIZATION):
        raise ValueError("Pilot normalization receipt changed")
    recs = _stage(root, IDENTITY, "identity", "observations")
    observations = {r["record_id"]: r for r in recs}
    raw_members = _stage(root, IDENTITY, "identity", "members")
    map_site = {m["record_id"]: m["site_id"] for m in raw_members}
    if (
        len(observations) != len(recs)
        or len(raw_members) != len(map_site)
        or not map_site.keys() <= observations.keys()
    ):
        raise ValueError("Invalid identity observations or memberships")
    source_data, source_ids, roles = _source_records(root, pilot, observations, map_site)
    ids = _stage(root, IDENTITY, "identity", "sites")
    original = _stage(root, MATURITY, "maturity", "sites")
    reviewed = _stage(root, MATURITY, "maturity", "reviewed-sites")
    changes = _stage(root, MATURITY, "maturity", "corrections")
    qa = bound(root, QA, QA_DISPOSITIONS)
    sites = _join_sites(ids, observations, original, reviewed, changes, qa, roles)
    effective, production_audit = reviewed_for_release(root, ids, reviewed)
    sites = _publication_sites(sites, reviewed, effective, production_audit)
    by_site = {s["site_id"]: s for s in sites}
    if sum(len(s["record_ids"]) for s in sites) != len(raw_members) or any(
        any(map_site[r] != s["site_id"] for r in s["record_ids"]) for s in sites
    ):
        raise ValueError("Canonical memberships fail source reconciliation")
    if load(root, MATURITY)["post_assessment_corrections"] != len(changes):
        raise ValueError("Incomplete post-assessment corrections")
    for s in export_tables["site"]:
        canonical_site = by_site[s["site_id"]]
        if not canonical_site["distributable"] or any(
            s[k] != canonical_site[k]
            for k in ("maturity_rung", "lat", "lon", "review_state", "record_ids")
        ):
            raise ValueError("Export site differs from reviewed canonical site")
    members = []
    for m in raw_members:
        r = observations[m["record_id"]]
        e = {
            **r["evidence"],
            "source_id": r["source_id"],
            "source_url": r["source_url"],
            "record_id": r["record_id"],
        }
        members.append(
            {
                "site_id": m["site_id"],
                "record_id": m["record_id"],
                "method": m["method"],
                "match_score": None,
                "identity_decisions": m["decision_pair_ids"],
                **_facts(e),
                "evidence": e,
            }
        )
    registry = source_registry(root)
    operators = ops(root, registry)
    docs = {}
    for m in effective:
        for ev in [m["rung_evidence"], m["status_evidence"], *m["reporting_evidence"]]:
            if ev and ev.get("verified_document"):
                rec = verified_citation(ev, registry)
                doc = ev["verified_document"]
                docs[rec["source_id"]] = doc["sha256"]
                registry[rec["source_id"]]["checksum"] = doc["sha256"]
    for sid, meta in load(root, "overrides/pilot_operator_documents.json").items():
        registry[sid]["checksum"] = meta["sha256"]
    registry_ids = (
        set(source_ids.values())
        | docs.keys()
        | {item[0]["source_id"] for item in operators.values()}
    )
    for s in atlas["sources"]:
        if s["id"] not in registry:
            registry[s["id"]] = dict(s)
    context = _context(root, source_ids, by_site.keys(), registry)
    registry_ids |= {row["source_id"] for rows in context.values() for row in rows}
    registry_ids |= {
        e["source_id"] for rows in context.values() for row in rows for e in row["evidence"]
    }
    facilities = _facility_rows(sites, observations, operators, registry)
    rows = {
        "source": _sources(root, registry, registry_ids, export_tables["source"]),
        "site": sites,
        "site_member": members,
        "facility": facilities,
        **context,
    }
    if set(rows) | {"source_record"} != set(TABLES):
        raise ValueError("Incomplete private canonical inventory")
    if {s["id"] for s in atlas["sites"]} - by_site.keys():
        raise ValueError("Catalog includes an unknown canonical site")
    result = {
        f"{PREFIX}/{name}.parquet": _parquet(name, values, spatial=name in {"site", "facility"})
        for name, values in rows.items()
    }
    result[f"{PREFIX}/source_record.parquet"] = source_data
    return result
