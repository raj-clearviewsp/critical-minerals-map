"""Private master records, corrected maturity evidence, and off-map retention."""

import json
from copy import deepcopy

import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from shapely import from_wkb

from pipeline.release_products.canonical import _context, _join_sites, _parquet, _source_records
from pipeline.release_products.common import BASE, CONTEXT, metadata


def pilot(root):
    schema = pa.schema(
        [
            ("record_id", pa.string()),
            ("source_id", pa.string()),
            ("raw_name", pa.string()),
            ("lat", pa.float64()),
            ("lon", pa.float64()),
            ("location_precision", pa.string()),
            ("pilot_evidence", pa.string()),
            ("raw_fields", pa.string()),
            ("source_row", pa.int64()),
            ("registered_at", pa.string()),
            ("retrieved_at", pa.string()),
        ],
        metadata={b"atlas_schema": b"pilot-source-record-v1"},
    )
    rows = [
        {"record_id": "s:1", "raw_name": "Cedar", "lat": 2.0, "lon": 3.0, "source_row": 1},
        {"record_id": "s:2", "raw_name": "Unlocated", "source_row": 2},
        {
            "record_id": "s:3",
            "raw_name": "Reported national point",
            "lat": 5.0,
            "lon": 6.0,
            "location_precision": "country_centroid",
            "source_row": 3,
        },
    ]
    rows = [
        {
            **r,
            "source_id": "s",
            "registered_at": "2026-10-31",
            "retrieved_at": None,
            "pilot_evidence": '[{"role":"primary","codes":["Ni"]}]',
            "raw_fields": '{"original": "text"}',
        }
        for r in rows
    ]
    rel = "data/staging/pilot/s.parquet"
    path = root / rel
    path.parent.mkdir(parents=True)
    pq.write_table(pa.Table.from_pylist(rows, schema=schema), path)
    return {
        "kept_rows": 3,
        "sources": [
            {
                "artifact": rel,
                "source_id": "s",
                "kept_rows": 3,
                "sha256": metadata(path.read_bytes())["sha256"],
            }
        ],
    }, schema


def test_preserves_original_fields_types_nulls_and_coordinates(tmp_path):
    receipt, schema = pilot(tmp_path)
    data, ids, roles = _source_records(
        tmp_path, receipt, {"s:1": {"review_state": "single_source_provisional"}}, {"s:1": "site"}
    )
    result = pq.read_table(pa.BufferReader(data))
    original = pq.read_table(tmp_path / receipt["sources"][0]["artifact"])
    assert result.select(schema.names).equals(original)
    assert set(ids) == {"s:1", "s:2", "s:3"} and roles == {"s:1": [("primary", "Ni")]}
    assert (
        result["site_id"].to_pylist() == ["site", None, None]
        and result["retrieved_at"].null_count == 3
    )
    geo = result["geometry"].to_pylist()
    assert [(from_wkb(x).x, from_wkb(x).y) if x else None for x in geo] == [(3.0, 2.0), None, None]
    assert (
        json.loads(result.schema.metadata[b"geo"])["columns"]["geometry"]["crs"]["id"]["code"]
        == "CRS84"
    )
    receipt["sources"][0]["sha256"] = "0" * 64
    with pytest.raises(ValueError, match="changed"):
        _source_records(tmp_path, receipt, {}, {})


def site_fixture():
    site = {
        "site_id": "stable",
        "name": "Cedar",
        "lat": 2.0,
        "lon": 3.0,
        "country_iso3": "AUS",
        "record_ids": ["s:1"],
        "coordinate_record_id": "s:1",
        "commodities": ["Ni"],
        "review_state": "single_source_provisional",
        "location_precision": None,
        "decision_pair_ids": [],
        "provenance": {"source_row": 1},
    }
    observation = {
        "record_id": "s:1",
        "site_id": "stable",
        "source_url": "https://example.test/source",
        "source_id": "s",
        "lat": 2.0,
        "lon": 3.0,
        "aliases": [],
        "deposit_types": [],
        "evidence": {"retrieved_at": None, "evidence_grade": "B"},
    }
    grade = {
        "site_id": "stable",
        "source_record_ids": ["s:1"],
        "maturity_rung": 1,
        "maturity_label": "Occurrence",
        "national_system": False,
        "rung_evidence": {"record_id": "s:1", "reason": "Site reported"},
        "status": "unknown",
        "status_as_of": None,
        "status_evidence": None,
        "status_observations": [],
        "reporting_evidence": [],
        "reporting_system": None,
        "limitations": [],
        "as_of": "2026-11-04",
    }
    corrected = {
        **grade,
        "maturity_rung": 2,
        "maturity_label": "Historical estimate",
        "rung_evidence": {"record_id": "s:1", "reason": "Historical estimates verified"},
    }
    correction = {
        "site_id": "stable",
        "source_record_ids": ["s:1"],
        "original_rung": 1,
        "reviewed_rung": 2,
        "evidence": [corrected["rung_evidence"]],
    }
    qa = {
        "site_id": "stable",
        "geography": "outside_tolerance",
        "geographic_pass": False,
        "distributable": False,
        "download_holds": ["retrieval_date", "outside_tolerance"],
    }
    return (
        [site],
        {"s:1": observation},
        [grade],
        [corrected],
        [correction],
        [qa],
        {"s:1": [("primary", "Ni")]},
    )


def test_private_site_preserves_corrected_rung_and_geographic_hold():
    inputs = site_fixture()
    rows = _join_sites(*inputs)
    table = pq.read_table(pa.BufferReader(_parquet("site", rows, spatial=True)))
    row = table.to_pylist()[0]
    assert (
        row["maturity_rung"] == 2
        and row["original_maturity_rung"] == 1
        and row["distributable"] is False
    )
    assert json.loads(row["correction"])["source_record_ids"] == ["s:1"] and row[
        "primary_commodities"
    ] == ["Ni"]
    assert from_wkb(row["geometry"]).x == 3.0 and row["retrieved_at"] is None
    inputs[4].clear()
    with pytest.raises(ValueError, match="coverage"):
        _join_sites(*inputs)


def test_duplicate_source_ids_or_absent_identity_record_abort(tmp_path):
    receipt, _ = pilot(tmp_path)
    with pytest.raises(ValueError, match="reconcile"):
        _source_records(tmp_path, receipt, {"absent": {}}, {})
    duplicate = deepcopy(receipt)
    duplicate["sources"] *= 2
    duplicate["kept_rows"] = 6
    with pytest.raises(ValueError, match="inventory"):
        _source_records(tmp_path, duplicate, {}, {})


def test_context_retains_private_financing_without_assigning_site(tmp_path):
    ev = {
        "record_id": "s:1",
        "source_id": "s",
        "source_url": "https://example.test/source",
        "retrieved_at": None,
        "evidence_grade": "C",
        "raw_sha256": "c" * 64,
    }
    rows = {
        "country_commodity": [],
        "export_restrictions": [],
        "china_official_finance": [
            {
                "aiddata_record_id": 109,
                "country_iso3": "AUS",
                "source_evidence": ev,
                "site_id": None,
                "year": 2017,
                "title": "Contribution",
                "amount_constant_usd_2023": 12.5,
                "original_currency": "AUD",
                "amount_original_currency": 20,
                "lender": "Original lender",
                "commodity_families": ["Ni"],
            }
        ],
    }
    artifacts = {}
    for name, v in rows.items():
        rel = f"{BASE}/context/{name}.json"
        f = tmp_path / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(json.dumps(v))
        artifacts[rel] = metadata(f.read_bytes())
    p = tmp_path / CONTEXT
    p.parent.mkdir(parents=True)
    p.write_text(
        json.dumps({"counts": {k: len(v) for k, v in rows.items()}, "artifacts": artifacts})
    )
    ctx = _context(tmp_path, {"s:1": "s"}, set())
    finance = ctx["china_official_finance"][0]
    assert (
        finance["site_id"] is None
        and finance["source_payload"]["original_currency"] == "AUD"
        and finance["retrieved_at"] is None
    )
    assert (
        pq.read_table(pa.BufferReader(_parquet("china_official_finance", [finance]))).to_pylist()[
            0
        ]["amount"]
        == 12.5
    )
    with pytest.raises(ValueError, match="outside"):
        _context(tmp_path, {}, set())


def test_private_registry_uses_normalization_snapshot_when_newer_one_exists(tmp_path):
    from pipeline.release_products.canonical import NORMALIZATION, _sources

    rel = "data/raw/s/2026-09-01/manifest.json"
    old = tmp_path / rel
    old.parent.mkdir(parents=True)
    old.write_text('{"captured":"old"}')
    newer = tmp_path / "data/raw/s/2026-10-01/manifest.json"
    newer.parent.mkdir(parents=True)
    newer.write_text('{"captured":"new"}')
    checksum = metadata(old.read_bytes())["sha256"]
    receipt = tmp_path / NORMALIZATION
    receipt.parent.mkdir(parents=True)
    receipt.write_text(json.dumps({"sources": [{"source_id": "s", "manifest_sha256": checksum}]}))
    registry = {
        "s": {
            "title": "Original source",
            "url": "https://example.test/source",
            "source_manifest_sha256": checksum,
            "raw_manifest_path": rel,
            "retrieved_at": None,
            "grade": "B",
        }
    }
    rec = _sources(tmp_path, registry, {"s"}, [])[0]
    assert (
        rec["checksum"] == checksum
        and rec["checksum_basis"] == "snapshot_manifest"
        and rec["retrieved_at"] is None
    )
    registry["s"]["raw_manifest_path"] = str(newer.relative_to(tmp_path))
    with pytest.raises(ValueError, match="normalization-bound"):
        _sources(tmp_path, registry, {"s"}, [])


def test_uncaptured_private_reference_retains_unknown_checksum(tmp_path):
    from pipeline.release_products.canonical import NORMALIZATION, _sources

    p = tmp_path / NORMALIZATION
    p.parent.mkdir(parents=True)
    p.write_text('{"sources":[]}')
    raw = {
        "title": "Indexed issuer disclosure",
        "url": "https://example.test/notice",
        "retrieved_at": None,
        "grade": "A",
        "redistribution_ok": False,
    }
    row = _sources(tmp_path, {"document": raw}, {"document"}, [])[0]
    assert (
        row["checksum"] is None
        and row["checksum_basis"] is None
        and row["source_metadata"]["archive_state"] == "citation_only"
    )
    with pytest.raises(ValueError, match="bound digest"):
        _sources(tmp_path, {"document": {**raw, "redistribution_ok": True}}, {"document"}, [])


def test_publication_correction_preserves_separate_step7_history_and_holds():
    from pipeline.release_products.canonical import _facility_rows, _publication_sites

    inputs = site_fixture()
    sites = _join_sites(*inputs)
    baseline = inputs[3]
    after = deepcopy(baseline)
    document = {
        "source_id": "issuer",
        "url": "https://example.test/filing",
        "sha256": "e" * 64,
        "retrieved_at": "2026-11-04",
        "evidence_grade": "A",
        "redistribution_ok": False,
    }
    ev = {"record_id": "s:1", "reason": "Dated mine production", "verified_document": document}
    after[0].update(
        maturity_rung=5,
        maturity_label="Operating",
        rung_evidence=ev,
        status="production_reported",
        status_as_of="2026-06-30",
        status_evidence=ev,
    )
    changed = {
        "site_id": "stable",
        "record_id": "s:1",
        "operator_source_id": "issuer",
        "baseline": {"maturity_rung": 2, "status": "unknown", "status_as_of": None},
        "baseline_rung_evidence": baseline[0]["rung_evidence"],
        "effective_rung": 5,
        "effective_status": "production_reported",
        "effective_status_as_of": "2026-06-30",
        "evidence": ev,
        "distribution_hold": "private_operator_document",
    }
    s = _publication_sites(sites, baseline, after, {"source_checks": [], "corrections": [changed]})[
        0
    ]
    assert (s["original_maturity_rung"], s["reviewed_maturity_rung"], s["maturity_rung"]) == (
        1,
        2,
        5,
    )
    assert (
        s["correction"]["reviewed_rung"] == 2
        and s["publication_correction"]["effective_rung"] == 5
        and not s["distributable"]
    )
    fac = _facility_rows([s], inputs[1], {}, {})
    assert (
        len(fac) == 1
        and fac[0]["source_id"] == "issuer"
        and fac[0]["source_url"] == document["url"]
        and "private_operator_document" in fac[0]["download_holds"]
    )
    with pytest.raises(ValueError, match="coverage"):
        _publication_sites(sites, baseline, after, {"source_checks": [], "corrections": []})
