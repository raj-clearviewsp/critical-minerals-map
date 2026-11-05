from pipeline.release_products.catalog import (
    add_policy_context,
    chapter_evidence,
    verified_citation,
)


def test_oecd_variation_is_distinct_from_quota_and_date():
    descriptions = {
        k: {"value": v, "record_id": k, "source_row": i}
        for i, (k, v) in enumerate(
            [("VAL", "0"), ("VAL_UNIT", "Tons"), ("DATI", "22/04/2016"), ("END", "14/03/2018")], 1
        )
    }
    ev = {
        "source_id": "oecd",
        "source_url": "https://example.org/oecd",
        "retrieved_at": "2026-10-29",
        "evidence_grade": "B",
        "raw_sha256": "a" * 64,
        "record_id": "main",
    }
    r = {
        "country_iso3": "AGO",
        "commodity_families": ["Ni"],
        "product_code": "7503",
        "measure_type": "quota",
        "year": 2016,
        "variation": "A1",
        "descriptions": descriptions,
        "source_evidence": ev,
    }
    profiles = {}
    t = {"export_restriction": [], "china_official_finance": []}
    add_policy_context(
        profiles,
        lambda code: profiles.setdefault(code, {"context": []}),
        [r],
        [],
        lambda *args: args,
        t,
        {"oecd": {"redistribution_ok": True}},
    )
    exported = t["export_restriction"][0]
    assert (exported["variation"], exported["value"], exported["unit"]) == ("A1", "0", "Tons")
    assert exported["end_date_raw"] == "14/03/2018"
    assert "END" in {e["record_id"] for e in exported["evidence"]}


def test_supporting_statement_uses_its_own_date_and_document_identity():
    reg = {}
    ev = {
        "record_id": "r",
        "source_currency": "2025-12",
        "verified_document": {
            "title": "2026 reserve update",
            "url": "https://example.org/statement",
            "verified_as_of": "2026-06-30",
            "locator": "Table 1",
            "evidence_grade": "A",
            "redistribution_ok": False,
        },
    }
    cited = verified_citation(ev, reg)
    assert cited["date"] == "2026-06-30" and "Table 1" in cited["note"]
    chapter = {
        "source_id": "mcs",
        "source_url": "https://example.org/nickel.pdf",
        "retrieved_at": "2026-11-04",
        "raw_sha256": "b" * 64,
        "evidence_grade": "B",
        "redistribution_ok": True,
        "license": "Public domain",
    }
    bound = chapter_evidence(chapter, reg, "Ni")
    assert bound["source_id"] != "mcs"
    assert reg[bound["source_id"]]["checksum"] == chapter["raw_sha256"]
