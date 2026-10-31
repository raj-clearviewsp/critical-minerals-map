"""Spatial completeness and human-review sampling guards, without model fitting."""

import copy
import math
import random
from collections import Counter

import pytest

from pipeline.link import candidates as module
from pipeline.link.candidates import (
    EARTH_RADIUS_KM,
    build_candidates,
    haversine_km,
    normalize_name,
    prepare_review,
    select_gold,
)


def asset(identity, lat=0.0, lon=0.0, **overrides):
    return {
        "record_id": identity,
        "source_id": "source_a",
        "raw_name": "Example Mine",
        "aliases": [],
        "lat": lat,
        "lon": lon,
        "country_iso3s": ["AUS"],
        "pilot_families": ["Ni"],
        "radius_km": 10,
        "precision_policy": "unknown precision; review only",
        "record_kind": "deposit",
        **overrides,
    }


def equatorial_longitude(km):
    return math.degrees(km / EARTH_RADIUS_KM)


@pytest.mark.parametrize("distance,expected", [(9.999999, 1), (10.0, 1), (10.000001, 0)])
def test_exact_distance_boundary(distance, expected):
    records = [asset("a"), asset("b", lon=equatorial_longitude(distance))]
    pairs = build_candidates(records)
    assert len(pairs) == expected
    if pairs:
        assert pairs[0]["distance_km"] <= 10


def test_pair_uses_the_smaller_radius_and_shared_family():
    near = asset("b", lon=equatorial_longitude(15), radius_km=25)
    assert not build_candidates([asset("a", radius_km=10), near])
    pairs = build_candidates([asset("a", radius_km=25), near])
    assert len(pairs) == 1 and pairs[0]["radius_km"] == 25
    assert not build_candidates([asset("a", pilot_families=["REE"]), asset("b")])
    pairs = build_candidates(
        [
            asset("a", pilot_families=["Ni", "Co"]),
            asset("b", pilot_families=["REE", "Co"], country_iso3s=["COD"]),
        ]
    )
    assert pairs[0]["shared_families"] == ["Co"]
    assert pairs[0]["left_country_iso3s"] != pairs[0]["right_country_iso3s"]


@pytest.mark.parametrize(
    "coordinates",
    [
        [(0, 179.99), (0, -179.99)],
        [(89.99, 0), (89.99, 180)],
        [(-89.99, 0), (-89.99, -180)],
        [(90, -180), (90, 180)],
    ],
)
def test_dateline_and_poles_have_no_spatial_seam(coordinates):
    pairs = build_candidates([asset(str(i), *point) for i, point in enumerate(coordinates)])
    assert len(pairs) == 1
    assert pairs[0]["distance_km"] < 3


def test_grid_matches_exhaustive_reference_including_edges():
    rng = random.Random(4107)
    records = []
    for anchor_lat, anchor_lon in [(0, 0), (30, 179.98), (89.97, 0), (-89.97, 0)]:
        for _ in range(25):
            lat = max(-90, min(90, anchor_lat + rng.uniform(-0.2, 0.2)))
            lon = (anchor_lon + rng.uniform(-0.3, 0.3) + 180) % 360 - 180
            records.append(
                asset(
                    str(len(records)),
                    lat,
                    lon,
                    radius_km=rng.choice([10, 25]),
                    pilot_families=rng.choice([["Ni"], ["Co"], ["Ni", "Co"]]),
                )
            )
    expected = set()
    for i, left in enumerate(records):
        for right in records[i + 1 :]:
            if set(left["pilot_families"]) & set(right["pilot_families"]):
                distance = haversine_km(left["lat"], left["lon"], right["lat"], right["lon"])
                if distance <= min(left["radius_km"], right["radius_km"]):
                    expected.add(tuple(sorted([left["record_id"], right["record_id"]])))
    pairs = build_candidates(records)
    actual = {(p["left_record_id"], p["right_record_id"]) for p in pairs}
    assert actual == expected
    assert len(pairs) == len(actual)


def test_grid_avoids_global_all_pairs_scan(monkeypatch):
    calls = 0
    original = module.haversine_km

    def counted(*args):
        nonlocal calls
        calls += 1
        return original(*args)

    monkeypatch.setattr(module, "haversine_km", counted)
    # 12,000 globally distributed records. This input has no nearby pairs.
    records = [
        asset(str(i * 120 + j), -75 + i * 1.5, -179 + j * 3) for i in range(100) for j in range(120)
    ]
    assert build_candidates(records) == []
    assert calls < len(records) * 2


def test_name_categories_use_aliases_without_predicted_labels():
    assert normalize_name("Søndré Méad PROJECT") == "sondre mead"
    assert normalize_name("Straße Mine") == "strasse"
    assert normalize_name("Mineshaft Project") == "mineshaft"
    assert normalize_name("Никель Mine") == "никель"
    records = [
        asset("a", raw_name="Søndré Méad Mine"),
        asset("b", raw_name="Source spelling", aliases=["SONDRE MEAD deposit"]),
    ]
    pair = build_candidates(records)[0]
    assert pair["name_category"] == "exact_normalized"
    assert pair["exact_normalized_name"] == "sondre mead"
    empty = build_candidates([asset("a", raw_name="Mine"), asset("b", raw_name="Deposit")])[0]
    assert empty["name_category"] == "missing_name"
    assert "score" not in pair and "predicted_label" not in pair
    variants = build_candidates(
        [
            asset("a", raw_name="East", aliases=["West"]),
            asset("b", raw_name="East Ridge", aliases=["West Hill"]),
        ]
    )[0]
    assert variants["name_category"] == "one_shared_token"


def review_assets():
    records = []
    sources = [
        ("a", "a"),
        ("a", "b"),
        ("a", "c"),
        ("a", "d"),
        ("b", "b"),
        ("b", "c"),
        ("b", "d"),
        ("c", "c"),
        ("c", "d"),
        ("d", "d"),
    ]
    names = [
        ("São Bento Mine", "Sao Bento deposit"),
        ("Red Ridge East", "Red Ridge West"),
        ("Alpha East", "Alpha West"),
        ("North", "South"),
        (None, "Named deposit"),
    ]
    for i in range(300):
        lat, lon = -60 + (i // 20) * 7, -170 + (i % 20) * 17
        distance = [0.5, 2, 7, 16][i % 4]
        lon_delta = equatorial_longitude(distance) / math.cos(math.radians(lat))
        source_a, source_b = sources[i % len(sources)]
        name_a, name_b = names[i % len(names)]
        families = [["Ni"], ["Co"], ["REE"], ["Ni", "Co"]][i % 4]
        records.extend(
            [
                asset(
                    f"{i:03d}-a",
                    lat,
                    lon,
                    source_id=source_a,
                    raw_name=name_a,
                    pilot_families=families,
                    radius_km=25,
                ),
                asset(
                    f"{i:03d}-b",
                    lat,
                    lon + lon_delta,
                    source_id=source_b,
                    raw_name=name_b,
                    pilot_families=families,
                    radius_km=25,
                ),
            ]
        )
    return records


def test_exactly_200_blank_unique_pairs_with_reconciled_strata_and_coverage():
    result = prepare_review(review_assets())
    assert len(result["candidates"]) == 300
    pairs, metadata = result["pairs"], result["metadata"]
    assert len(pairs) == len({p["pair_id"] for p in pairs}) == 200
    assert all(p["label"] == p["reviewer"] == p["review_notes"] == "" for p in pairs)
    assert all(not c["unrepresented"] for c in metadata["coverage"].values())
    assert metadata["coverage"]["family"]["sampled"] == ["Co", "Ni", "REE"]
    counts = Counter(p["stratum_id"] for p in pairs)
    assert sum(s["candidate_count"] for s in metadata["strata"]) == 300
    assert sum(s["sample_quota"] for s in metadata["strata"]) == 200
    for stratum in metadata["strata"]:
        assert counts[stratum["stratum_id"]] == stratum["sample_quota"]
        assert stratum["sample_weight"] == stratum["candidate_count"] / stratum["sample_quota"]


def test_input_order_independent_ids_selection_and_no_input_mutation():
    records = review_assets()
    saved = copy.deepcopy(records)
    expected = prepare_review(records)
    random.Random(345).shuffle(records)
    actual = prepare_review(records)
    assert actual == expected
    assert sorted(records, key=lambda a: a["record_id"]) == saved
    shuffled = list(reversed(expected["candidates"]))
    assert select_gold(shuffled) == select_gold(expected["candidates"])
    alternate = select_gold(shuffled, seed="another-fixed-seed")
    assert {p["pair_id"] for p in alternate["pairs"]} != {p["pair_id"] for p in expected["pairs"]}


def test_sample_never_fabricates_or_repeats_candidate_pairs():
    one = build_candidates([asset("a"), asset("b")])
    with pytest.raises(ValueError, match="only 1"):
        select_gold(one)
    with pytest.raises(ValueError, match="Duplicate candidate"):
        select_gold([*one, *one], size=1)
    swapped = {**one[0], "pair_id": "different", "left_record_id": "b", "right_record_id": "a"}
    with pytest.raises(ValueError, match="duplicate unordered"):
        select_gold([*one, swapped], size=1)


def test_unsampled_strata_are_explicit_and_never_given_fictitious_weights():
    # More distinct source-pair strata than the sample can represent.
    candidates = []
    for i in range(205):
        candidates.extend(
            build_candidates(
                [
                    asset(f"{i}-a", source_id=f"source_{i}"),
                    asset(f"{i}-b", source_id=f"source_{i}"),
                ]
            )
        )
    result = select_gold(candidates)
    unsampled = [s for s in result["metadata"]["strata"] if not s["sample_quota"]]
    assert len(unsampled) == 5
    assert all(s["sample_weight"] is None for s in unsampled)
    assert len(result["metadata"]["coverage"]["source_pair"]["unrepresented"]) == 5
    assert len(result["pairs"]) == 200
    complete = select_gold(candidates, size=205)
    assert all(s["sample_weight"] == 1 for s in complete["metadata"]["strata"])


@pytest.mark.parametrize(
    "changes",
    [
        {"lat": float("nan")},
        {"lon": 181},
        {"lat": True},
        {"radius_km": 100},
        {"aliases": "not a list"},
        {"pilot_families": ["Cu"]},
        {"precision_policy": ""},
    ],
)
def test_invalid_eligible_asset_contract_fails_visibly(changes):
    with pytest.raises(ValueError):
        build_candidates([asset("a", **changes)])
    with pytest.raises(ValueError, match="Duplicate asset"):
        build_candidates([asset("a"), asset("a")])
