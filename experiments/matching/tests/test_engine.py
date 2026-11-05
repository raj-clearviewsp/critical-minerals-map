import copy

import pytest

from pipeline.matching_lab import core
from pipeline.matching_lab.audits import blocking_audit, cluster_audit
from pipeline.matching_lab.features import compare, names
from pipeline.matching_lab.metrics import evaluate, wilson
from pipeline.matching_lab.model import (
    cross_validate,
    fit,
    fold,
    reference_score,
    route,
    splink_scores,
)


def record(identifier="a", name="Mont Élan Mine", lat=45.0, lon=-80.0, **kwargs):
    return {
        "record_id": identifier,
        "source_id": "source_" + identifier,
        "primary_name": name,
        "display_name": name,
        "primary_name_usable": True,
        "identity_aliases": [],
        "context_names": [],
        "unverified_aliases": [],
        "entity_level": "site",
        "scope_flags": [],
        "pilot_families": ["Ni"],
        "country_iso3s": ["CAN"],
        "deposit_types": ["laterite"],
        "lat": lat,
        "lon": lon,
        **kwargs,
    }


def training():
    rows = []
    labels = {}
    for i in range(40):
        positive = i % 2 == 0
        feature = compare(
            record(), record("b", name="Mont Elan" if positive else "Completely Other")
        )
        rows.append({"pair_id": f"p{i}", "component": f"component{i // 2}", **feature})
        labels[f"p{i}"] = "same_site" if positive else "different_sites"
    return rows, labels


def test_unicode_generic_words_and_trusted_aliases():
    assert compare(record(), record("b", name="mont elan deposit"))["levels"]["name"] == 0
    assert (
        compare(record(), record("b", name="Other", identity_aliases=["Mont Elan"]))["levels"][
            "name"
        ]
        == 0
    )
    assert names(record(name="Łódź Øresund")) == ("lodz oresund",)
    assert names(record(name="白云鄂博")) == ("白云鄂博",)


@pytest.mark.parametrize("field", ["context_names", "unverified_aliases"])
def test_company_parent_or_unverified_name_cannot_create_identity_match(field):
    a = record()
    b = record("b", name="Different Asset", **{field: ["Mont Elan"]})
    assert compare(a, b)["levels"]["name"] != 0


@pytest.mark.parametrize("name", [None, "Unknown mine", "unnamed occurrence", "Nickel Mine", "   "])
def test_generic_or_missing_names_are_not_equality_evidence(name):
    feature = compare(record(name=name), record("b", name=name))
    assert feature["levels"]["name"] == 5
    assert route(feature, 0.999) == "scope_or_evidence_review"


@pytest.mark.parametrize(
    "field,value",
    [
        ("lat", None),
        ("lat", True),
        ("lat", 91),
        ("lon", 181),
        ("lat", float("nan")),
        ("lon", float("inf")),
    ],
)
def test_invalid_coordinates_force_review(field, value):
    feature = compare(record(), record("b", **{field: value}))
    assert feature["distance_km"] is None
    assert "invalid_or_missing_coordinates" in feature["guards"]
    assert route(feature, 0.999) == "scope_or_evidence_review"


def test_equator_and_antimeridian_are_valid_but_zero_sentinel_is_not():
    f = compare(record(lat=0, lon=179.999), record("b", lat=0, lon=-179.999))
    assert f["distance_km"] < 0.25
    assert "invalid_or_missing_coordinates" not in f["guards"]
    assert compare(record(), record("b", lat=0, lon=0))["distance_km"] is None


@pytest.mark.parametrize(
    "change,expected",
    [
        ({"entity_level": "unknown"}, "unresolved_entity_scope"),
        ({"entity_level": "district_area"}, "incompatible_entity_scope"),
        ({"scope_flags": ["publisher_cluster_record"]}, "publisher_cluster_record"),
        ({"scope_flags": ["qualified_entity_level"]}, "qualified_entity_level"),
        ({"country_iso3s": ["AUS"]}, "country_conflict"),
        ({"pilot_families": ["REE"]}, "commodity_conflict"),
        ({"pilot_families": []}, "missing_commodity_context"),
        ({"primary_name": "Mont Elan Plant"}, "extraction_processing_scope"),
        ({"primary_name": "Mont Elan North"}, "directional_feature_difference"),
        ({"primary_name": "Mont Elan 2"}, "numbered_feature_difference"),
    ],
)
def test_scope_guards_override_a_high_model_score(change, expected):
    feature = compare(record(), record("b", **change))
    assert expected in feature["guards"]
    assert route(feature, 0.999999) == "scope_or_evidence_review"


def test_directed_parent_relationship_is_preserved_and_symmetric_for_identity():
    a = record()
    b = record("b", name="North Working", parent_name="Mont Elan Mine", entity_level="subsite")
    ab, ba = compare(a, b), compare(b, a)
    assert ab == ba
    assert "explicit_parent_component" in ab["guards"]


@pytest.mark.parametrize(
    "score,expected",
    [
        (0.95, "auto_candidate"),
        (0.949, "identity_review"),
        (0.7, "identity_review"),
        (0.699, "keep_separate"),
    ],
)
def test_exact_threshold_boundaries(score, expected):
    assert route({"guards": []}, score) == expected


@pytest.mark.parametrize("score", [-0.1, 1.1, float("nan"), float("inf")])
def test_invalid_scores_rejected(score):
    with pytest.raises(ValueError):
        route({"guards": []}, score)


def test_supervised_splink_matches_independent_calculation_and_row_order():
    rows, labels = training()
    model = fit(rows, labels)
    scores = splink_scores(model, rows)
    reverse = splink_scores(model, list(reversed(rows)))
    for row in rows:
        assert scores[row["pair_id"]] == pytest.approx(reference_score(model, row), abs=1e-12)
        assert scores[row["pair_id"]] == pytest.approx(reverse[row["pair_id"]], abs=1e-12)
    for rates in model["comparisons"].values():
        assert sum(rates["m"]) == pytest.approx(1)
        assert sum(rates["u"]) == pytest.approx(1)
        assert min(*rates["m"], *rates["u"]) > 0


def test_abstentions_do_not_train_and_containment_is_not_same_site():
    rows, labels = training()
    labels["p0"] = "related_scope_unclear"
    labels["p1"] = "insufficient_evidence"
    labels["p2"] = "left_contains_right"
    model = fit(rows, labels)
    assert model["training_counts"] == {"positive": 18, "negative": 20, "abstention": 2}
    altered = copy.deepcopy(rows)
    altered[0]["levels"] = {"name": 5, "distance": 5, "scope": 3, "geology": 2}
    assert fit(altered, labels) == model


def test_fit_rejects_unknown_extra_or_missing_labels():
    rows, labels = training()
    with pytest.raises(ValueError):
        fit(rows, {**labels, "holdout": "same_site"})
    with pytest.raises(ValueError):
        fit(rows, {**labels, "p0": "maybe"})
    with pytest.raises(ValueError):
        fit(rows[:-1], labels)


def test_cross_validation_excludes_whole_neighborhoods():
    rows, labels = training()
    scores, details = cross_validate(rows, labels)
    assert set(scores) == set(labels)
    assert sum(d["test"] for d in details) == 40
    for n in range(5):
        train = {r["component"] for r in rows if fold(r["component"]) != n}
        test = {r["component"] for r in rows if fold(r["component"]) == n}
        assert not train & test


def test_zero_predictions_have_undefined_precision_and_explicit_abstentions():
    rows, labels = training()
    labels["p0"] = "related_scope_unclear"
    result = evaluate(rows, {r["pair_id"]: 0.1 for r in rows}, labels)
    assert result["precision"] is None and result["precision_wilson95"] is None
    assert result["abstentions"] == 1 and result["resolved_identity_cases"] == 39
    assert result["recall_within_resolved_sample"] == 0
    assert wilson(0, 0) is None
    assert wilson(1, 1)[0] < 0.97
    assert wilson(97, 100)[0] < 0.97


def test_high_score_unresolved_is_not_counted_as_correct():
    rows, labels = training()
    labels = {p: "related_scope_unclear" for p in labels}
    result = evaluate(rows, {p: 0.999 for p in labels}, labels)
    assert result["precision"] is None
    assert result["unresolved_auto_candidates"] == len(rows)


def test_transitive_chain_is_quarantined_when_endpoints_disagree():
    rows, labels = training()
    model = fit(rows, labels)
    a = record("a", name="Alpha")
    b = record("b", name="Alpha", identity_aliases=["Beta"])
    c = record("c", name="Beta")
    records = {r["record_id"]: r for r in [a, b, c]}
    edges = [
        {"pair_id": "ab", "left_record_id": "a", "right_record_id": "b", **compare(a, b)},
        {"pair_id": "bc", "left_record_id": "b", "right_record_id": "c", **compare(b, c)},
    ]
    result = cluster_audit(edges, {"ab": 0.999, "bc": 0.999}, records, model)
    assert result["proposed_components"] == 1
    assert result["quarantined_components"] == 1
    assert result["actual_merges"] == 0


def test_wider_block_audit_detects_missing_candidates_without_claiming_matches():
    a, b = record("a"), record("b", lat=45.15)
    result = blocking_audit({"a": a, "b": b}, [])
    assert result["missing_candidates"] == 1
    assert result["missing_within10km"] == 0
    assert result["measured_blocking_recall"] is None


def test_frozen_writer_never_overwrites_or_changes_noop_mtime(tmp_path):
    core.save(tmp_path, {"x.json": b"old"})
    mtime = (tmp_path / "x.json").stat().st_mtime_ns
    core.save(tmp_path, {"x.json": b"old"})
    assert (tmp_path / "x.json").stat().st_mtime_ns == mtime
    with pytest.raises(ValueError):
        core.save(tmp_path, {"new.json": b"new", "x.json": b"changed"})
    assert not (tmp_path / "new.json").exists()


def test_training_loader_never_opens_holdout_answers(tmp_path, monkeypatch):
    rows, labels = training()
    # Enlarge the synthetic development design to120; no private data is used.
    benchmark = [
        {"pair_id": f"p{i}", "split": "development", "split_component": f"c{i}"} for i in range(120)
    ]
    benchmark += [{"pair_id": "sealed", "split": "holdout", "split_component": "heldout"}]
    old = [
        {
            "pair_id": f"p{i}",
            "left_record_id": f"l{i}",
            "right_record_id": f"r{i}",
            "label": "same_site",
        }
        for i in range(120)
    ]
    candidates = [
        {
            "pair_id": r["pair_id"],
            "left_record_id": r["left_record_id"],
            "right_record_id": r["right_record_id"],
        }
        for r in old
    ]
    calls = []

    def fake_bound(root, receipt, name):
        calls.append(name)
        assert not name.endswith("holdout.csv")
        assert not name.endswith("next60-completed-v1/latest-decisions.json")
        if name.endswith("records.json"):
            return b"{}"
        if name.endswith("triage-pairs.json"):
            return core.encoded(candidates)
        if name.endswith("benchmark.json"):
            return core.encoded({"cases": benchmark})
        if name.endswith("latest-decisions.json"):
            return core.encoded({"decisions": old})
        return b"pair_id,label\n"

    monkeypatch.setattr(core, "bound", fake_bound)
    _, _, _, loaded, _ = core.training_inputs(tmp_path)
    assert len(loaded) == 120 and "sealed" not in loaded
    assert len(calls) == 5
