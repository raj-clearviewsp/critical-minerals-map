import copy

import pytest

from pipeline.release_products.common import sha
from pipeline.release_products.production_evidence import (
    PUBLISHER_URLS,
    SCOPES,
    _apply_documents,
    _validate_document,
)

MP = "mp_materials_q2_2026"
RID = "usgs_global_ree:Global_REE:1343"
DEFERRED = "usgs_us_critical:deposits:339"
PRODUCTION = "REO Production Volume (MTs) 11,072"
REPORT = (
    "<html><title>MP Materials Corp. Form 10-Q</title><body>For the quarterly period ended "
    "June 30, 2026. The company owns and operates the Mountain Pass Rare Earth Mine in "
    "San Bernardino County, California.<table><tr><td>REO Production Volume (MTs)</td>"
    "<td>11,072</td></tr></table></body></html>"
)


def mp_document(html=REPORT):
    raw = html.encode()
    item = {
        "source_id": MP,
        "title": "MP Materials Form 10-Q for the quarter ended June 30, 2026",
        "reporting_period": "2026-06-30",
        "evidence_grade": "A",
        "locator": "Materials segment, Q2 2026 production table",
        "production_families": ["REE"],
        "record_ids": [RID, DEFERRED],
        "url": PUBLISHER_URLS[MP],
    }
    meta = {
        "source_id": MP,
        "url": item["url"],
        "final_url": item["url"],
        "access": "archived_document",
        "sha256": sha(raw),
        "bytes": len(raw),
        "retrieved_at": "2026-11-05T06:40:00Z",
    }
    return item, meta, raw


def mp_register():
    site = {
        "site_id": "mountain",
        "name": "Mountain Pass",
        "country_iso3": "USA",
        "lat": 35.4794,
        "lon": -115.5334,
        "record_ids": [RID],
    }
    baseline = {
        "site_id": "mountain",
        "as_of": "2026-11-04",
        "source_record_ids": [RID],
        "maturity_rung": 2,
        "status": "past_producer",
        "status_as_of": "2018",
        "rung_evidence": {"record_id": RID, "reason": "Historical resource"},
        "status_evidence": {"record_id": RID, "reason": "Past producer in 2018"},
        "status_observations": [{"status": "past_producer", "as_of": "2018"}],
    }
    obs = {
        "record_id": RID,
        "source_id": "usgs_global_ree",
        "site_id": "mountain",
        "name": "Mountain Pass",
        "country": "USA",
        "lat": site["lat"],
        "lon": site["lon"],
        "evidence": {"raw_sha256": "a" * 64, "retrieved_at": None, "source_currency": "2018"},
        "source_url": "https://www.usgs.gov/",
    }
    deferred = {
        **obs,
        "record_id": DEFERRED,
        "source_id": "usgs_us_critical",
        "site_id": None,
        "name": "Mountain Pass deposit",
    }
    return site, baseline, {RID: obs, DEFERRED: deferred}


def test_exact_production_correction_preserves_the_sealed_baseline_and_document_boundary():
    site, baseline, obs = mp_register()
    original = copy.deepcopy(baseline)
    doc = _validate_document(MP, *mp_document())
    rows, audit = _apply_documents([site], [baseline], obs, {MP: doc})
    r = rows[0]
    assert baseline == original
    assert (r["maturity_rung"], r["status"], r["status_as_of"]) == (
        5,
        "production_reported",
        "2026-06-30",
    )
    assert r["status_observations"][:-1] == original["status_observations"]
    assert r["rung_evidence"]["source_id"] == "usgs_global_ree"
    assert r["rung_evidence"]["source_currency"] == "2018"
    verified = r["rung_evidence"]["verified_document"]
    assert verified["source_id"] == MP and verified["evidence_grade"] == "A"
    assert verified["verified_as_of"] == "2026-06-30"
    assert verified["retrieved_at"] == "2026-11-05T06:40:00Z"
    assert (
        r["rung_evidence"]["redistribution_ok"] is False and verified["redistribution_ok"] is False
    )
    assert audit["corrections"][0]["baseline_rung_evidence"] == original["rung_evidence"]
    assert audit["corrections"][0]["distribution_hold"] == "private_operator_document"
    checks = {b["record_id"]: b for b in audit["source_checks"][0]["bindings"]}
    assert checks[DEFERRED]["outcome"] == "no_physical_site"


@pytest.mark.parametrize(
    "replacement",
    [
        "Planned annual output target of 60,000 MTs.",
        f"<script>{PRODUCTION}</script>",
        f'<div style="display:none"><span>{PRODUCTION}</span></div>',
    ],
)
def test_intent_or_hidden_text_cannot_supply_actual_production(replacement):
    html = REPORT.replace(
        "<table><tr><td>REO Production Volume (MTs)</td><td>11,072</td></tr></table>", replacement
    )
    with pytest.raises(ValueError, match="substantiate"):
        _validate_document(MP, *mp_document(html))


@pytest.mark.parametrize(
    "field,value",
    [
        ("sha256", "0" * 64),
        ("bytes", 1),
        ("url", "https://example.org/unrelated"),
        ("source_id", "different_asset"),
    ],
)
def test_document_binding_rejects_changed_archive_metadata(field, value):
    item, meta, raw = mp_document()
    meta[field] = value
    with pytest.raises(ValueError, match="checksum differs"):
        _validate_document(MP, item, meta, raw)


@pytest.mark.parametrize(
    "field,value",
    [
        ("title", "Wrong filing"),
        ("reporting_period", "2025-06-30"),
        ("record_ids", [RID, "jasansky:COM00363.00"]),
    ],
)
def test_document_binding_rejects_changed_curation(field, value):
    item, meta, raw = mp_document()
    item[field] = value
    with pytest.raises(ValueError):
        _validate_document(MP, item, meta, raw)


@pytest.mark.parametrize(
    "field,value",
    [
        ("lat", 46.75),
        ("country", "CAN"),
        ("name", "Mountain Pass district"),
        ("site_id", "another"),
    ],
)
def test_matching_an_id_alone_cannot_override_geographic_or_entity_scope(field, value):
    site, baseline, obs = mp_register()
    obs[RID][field] = value
    with pytest.raises(ValueError):
        _apply_documents([site], [baseline], obs, {MP: _validate_document(MP, *mp_document())})


@pytest.mark.parametrize("baseline_date", ["2026", "2026-06", "2026-07-03"])
def test_overlapping_or_newer_closure_is_preserved(baseline_date):
    site, baseline, obs = mp_register()
    baseline.update(status="closed", status_as_of=baseline_date)
    rows, audit = _apply_documents(
        [site], [baseline], obs, {MP: _validate_document(MP, *mp_document())}
    )
    assert rows == [baseline] and audit["corrections"] == []


@pytest.mark.parametrize(
    "source,outcome",
    [
        ("lynas_mt_weld", "undated_operator_context"),
        ("bhp_olympic_dam", "corroborates_baseline"),
        ("talon_eagle_q2_2026", "corroborates_baseline"),
    ],
)
def test_undated_source_and_existing_operating_evidence_keep_their_distinct_scopes(source, outcome):
    scope = SCOPES[source]
    sites, baselines, observations = [], [], {}
    for i, rid in enumerate(scope.record_ids):
        sid = f"site_{i}"
        sites.append(
            {
                "site_id": sid,
                "record_ids": [rid],
                "name": scope.names[0],
                "country_iso3": scope.country,
                "lat": scope.point[0],
                "lon": scope.point[1],
            }
        )
        baselines.append(
            {
                "site_id": sid,
                "source_record_ids": [rid],
                "as_of": "2026-11-04",
                "maturity_rung": 1 if source == "lynas_mt_weld" else 5,
                "status": "unknown" if source == "lynas_mt_weld" else "operating",
                "status_as_of": "2025-12",
            }
        )
        observations[rid] = {
            "record_id": rid,
            "source_id": rid.split(":", 1)[0],
            "site_id": sid,
            "name": scope.names[0],
            "country": scope.country,
            "lat": scope.point[0],
            "lon": scope.point[1],
        }
    doc = {"source_id": source, "production_families": list(scope.families)}
    rows, audit = _apply_documents(sites, baselines, observations, {source: doc})
    assert rows == baselines and audit["corrections"] == []
    assert all(b["outcome"] == outcome for b in audit["source_checks"][0]["bindings"])
    if source == "bhp_olympic_dam":
        assert not audit["source_checks"][0]["production_families"]
