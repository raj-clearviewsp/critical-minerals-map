from shapely.geometry import box

from pipeline.release_products.geography import check_point
from pipeline.release_products.quality import evidence_holds


def test_source_country_and_tolerance_never_reassign_country():
    world = {"AAA": box(0, 0, 1, 1), "BBB": box(1, 0, 2, 1)}
    assert check_point(0.5, 0.5, "AAA", world) == "inside_country"
    assert check_point(1.1, 0.5, "AAA", world) == "within_25_km"
    assert check_point(1.4, 0.5, "AAA", world) == "outside_tolerance"
    assert check_point(0.5, 0.5, None, world) == "country_unverified"
    assert check_point(0, 0, "AAA", world) == "invalid_coordinate"


def test_dateline_tolerance():
    assert check_point(179.99, 10, "AAA", {"AAA": box(-180, 9, -179.8, 11)}) == "within_25_km"


def test_permission_and_evidence_gates_are_independent():
    ev = {
        "source_id": "a",
        "source_url": "https://example.org/record",
        "retrieved_at": "2026-11-04",
        "raw_sha256": "a" * 64,
        "evidence_grade": "B",
    }
    assert not evidence_holds(ev, {"a": {"redistribution_ok": True}})
    assert evidence_holds({**ev, "retrieved_at": None}, {"a": {"redistribution_ok": False}}) == {
        "source_terms",
        "retrieval_date",
    }
    assert "document_terms" in evidence_holds(
        {
            **ev,
            "verified_document": {
                "url": ev["source_url"],
                "sha256": "b" * 64,
                "retrieved_at": "2026-11-04",
                "redistribution_ok": False,
            },
        },
        {"a": {"redistribution_ok": True}},
    )
