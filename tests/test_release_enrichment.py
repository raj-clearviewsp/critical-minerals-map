import json
from pathlib import Path

import pytest

from pipeline.release_enrichment import maturity
from pipeline.release_enrichment.common import load


@pytest.fixture(autouse=True)
def synthetic_site_authority(tmp_path, monkeypatch):
    # Exercise the exact-identity gate in public CI without restricted evidence.
    facts = []
    for name, category in (
        ("Synthetic Alpha", "proved_and_probable_reserve"),
        ("Synthetic Beta", "indicated_and_inferred_resource"),
    ):
        facts.append(
            {
                "site": name,
                "record_ids": [f"ga_critical_2025:{name}"],
                "reporting_system": "JORC 2012",
                "classification": category,
                "category_values": [{"tonnes_million": 2, "grade_pct": 1}],
                "verified_as_of": "2026-06-30",
                "statement_date": "2026-06-30",
                "redistribution_ok": False,
            }
        )
    path = tmp_path / "synthetic-statements.json"
    path.write_text(json.dumps(facts))
    monkeypatch.setattr(maturity, "STATEMENT_FILE", path)


def member(source, raw, name="Test deposit", **extra):
    return {
        "record_id": f"{source}:test",
        "source_id": source,
        "raw_name": name,
        "raw_fields": raw,
        "source_table": "table",
        "source_row": 12,
        "source_file": "immutable.csv",
        "source_url": "https://example.org/data",
        "raw_sha256": "a" * 64,
        "source_currency": "as published",
        "retrieved_at": None,
        "evidence_grade": "B",
        "redistribution_ok": True,
        "raw_commodities": {"commodity": "Ni"},
        "raw_status": {},
        "raw_size_grade_text": {},
        "record_kind": "deposit",
        "country": None,
        **extra,
    }


def assessed(*rows):
    return maturity.assess_site(
        {"site_id": "example"}, list(rows), load(maturity.CHAPTER_FILE), maturity.statement_map()
    )


@pytest.mark.parametrize(
    "source,raw,status",
    [
        ("icmm", {"Asset Type": "Mine", "Confidence Factor": "High"}, "unknown"),
        (
            "jasansky",
            {"activity_status": "active", "activity_status_year": 2019, "production_end": 2021},
            "historically_active",
        ),
        ("wapha", {"Status": "Active"}, "historically_active"),
        ("usgs_mrds", {"dev_stat": "Past Producer"}, "past_producer"),
        ("gseu_crm", {"Deposit mining activity status": "operating"}, "historically_operating"),
        (
            "usgs_us_critical",
            {
                "rank": 5,
                "depcat": "CM-recent producer+resources",
                "prduction1": "Production restart underway (2026).",
            },
            "unknown",
        ),
    ],
)
def test_older_flags_and_planned_operations_do_not_establish_recent_production(source, raw, status):
    out = assessed(member(source, raw))
    assert out["maturity_rung"] == 1 and out["status"] == status
    assert out["rung_evidence"]["record_id"] == source + ":test"


def test_date_scoped_operation_and_care_and_maintenance_are_distinct():
    active = member("ga_critical_2025", {"Status": "Operating mine"})
    out = assessed(active)
    assert (out["maturity_rung"], out["status"], out["status_as_of"]) == (5, "operating", "2025-12")
    active["raw_fields"]["Status"] = "Mine - care and maintenance"
    out = assessed(active)
    assert (out["maturity_rung"], out["status"]) == (1, "care_and_maintenance")


def test_older_code_and_quantities_cannot_become_current_reserve():
    old = member(
        "usgs_laterites",
        {"ResourceTonnage": "35.6", "Nire grade%": "1.2", "ResourceStandard": "JORC"},
    )
    out = assessed(old)
    assert (out["maturity_rung"], out["reporting_system"]) == (2, "JORC")
    assert out["rung_evidence"]["fields"]["Nire grade%"] == "1.2"
    national = member(
        "gseu_crm",
        {
            "resource classification method": "RussianNAENCode",
            "resource category": "(RUS)A+B",
            "Resources - tonnage of commodity (t)": 123,
        },
    )
    out = assessed(national)
    assert out["national_system"] is True and out["maturity_rung"] == 2
    assert out["reporting_system"] == "Russian NAEN"


def test_exact_primary_statement_binding_and_separate_resource_reserve_classes():
    facts = load(maturity.STATEMENT_FILE)
    for fact, expected in zip(facts, [4, 3], strict=True):
        exact = member(
            "ga_critical_2025",
            {"Status": "Mineral deposit"},
            fact["site"],
            record_id=fact["record_ids"][0],
        )
        out = assessed(exact)
        assert out["maturity_rung"] == expected and out["reporting_system"] == "JORC 2012"
        assert out["rung_evidence"]["verified_document"]["statement_date"] == fact["statement_date"]
        assert out["rung_evidence"]["redistribution_ok"] is False
        exact["record_id"] += "other"
        assert assessed(exact)["maturity_rung"] == 1
    assert assessed(member("usgs_us_critical", {}, "Eagle mine"))["maturity_rung"] == 1
    exact = member("usgs_us_critical", {}, "Eagle mine", record_id="usgs_us_critical:deposits:628")
    assert assessed(exact)["maturity_rung"] == 5


def test_blinded_sample_is_reproducible_and_assessment_cannot_switch_cases():
    sites = [{"site_id": f"s{i}", "name": f"Deposit {i}"} for i in range(40)]
    by_site = {s["site_id"]: [member("usgs_mrds", {"dev_stat": "Occurrence"})] for s in sites}
    packet = maturity.spot_packet(sites, by_site)
    assert packet == maturity.spot_packet(list(reversed(sites)), by_site)
    assert len(packet["cases"]) == 30
    assert "assigned_rung" not in str(packet) and "maturity_rung" not in str(packet)
    decisions = [{"site_id": s["site_id"], "maturity_rung": 1} for s in sites]
    response = {
        "schema_version": "independent-maturity-assessment-v1",
        "sample_sha256": packet["sample_sha256"],
        "reviewer": "Separate reviewer",
        "blinded_to_proposed_rungs": True,
        "assessments": [
            {
                "site_id": c["site_id"],
                "rung": 1,
                "record_id": "usgs_mrds:test",
                "reason": "No grade, tonnage or dated operation recorded.",
            }
            for c in packet["cases"]
        ],
    }
    assert all(r["agree"] for r in maturity.validate_assessment(packet, response, decisions))
    response["assessments"][0]["site_id"] = "other"
    with pytest.raises(ValueError):
        maturity.validate_assessment(packet, response, decisions)


def test_reserve_overlay_requires_an_exact_independent_row_comparison(tmp_path):
    import zipfile

    from pipeline.release_enrichment import corrections
    from pipeline.scaffold import digest

    rows = (
        "id,facility_id,year,material,commodity,mineral_value_tonnes,commodity_value_tonnes,"
        "grade_ppm,source_id,comment\n"
        "7,facility1,2019,O.bulk,Me.Ni,4000,48,12000,doc1,\n"
        "8,facility1,2019,O.bulk,Me.Ni,5000,,,,\n"
        "9,facility1,2019,O.bulk,Me.Cu,1000,5,5000,doc1,\n"
        "10,another,2019,O.bulk,Me.Ni,1000,10,10000,doc1,\n"
    ).replace("5000,,,,", "5000,,,doc1,")
    archive = tmp_path / "data/raw/sample.zip"
    archive.parent.mkdir(parents=True)
    with zipfile.ZipFile(archive, "w") as z:
        z.writestr(corrections.RESERVES, rows)
        z.writestr(
            corrections.SOURCES,
            "source_id,sources,source_urls\ndoc1,Original report,https://example.org/report\n",
        )
    row = member(
        "jasansky",
        {},
        record_id="jasansky:facility1",
        source_file="sample.zip",
        raw_sha256=digest(archive.read_bytes()),
    )
    result = corrections.audit(tmp_path, {"local-site": [row]})
    assert result["eligible_archive_rows"] == 3
    assert result["eligibility_counts"] == {
        "qualifying_historical_estimate": 1,
        "different_commodity": 1,
        "missing_positive_grade": 1,
    }
    confirmation = {
        "schema_version": "independent-jasansky-reserve-review-v1",
        "reviewer": "Independent reviewer",
        "method": "Independently compared the original archive records and exact facility IDs.",
        **{
            k: result[k]
            for k in (
                "archive_sha256",
                "reserves_csv_sha256",
                "eligible_facilities",
                "eligible_archive_rows",
            )
        },
        "qualifying_rows": [
            {
                "facility_id": "facility1",
                "native_row_id": "7",
                "physical_csv_row": 2,
                "year": "2019",
                "commodity": "Me.Ni",
                "ore_tonnes": "4000",
                "grade_ppm": "12000",
            }
        ],
    }
    base = [{"site_id": "local-site", "source_record_ids": [row["record_id"]], "maturity_rung": 1}]
    corrected, changes = corrections.overlay(base, result, confirmation)
    assert base[0]["maturity_rung"] == 1 and corrected[0]["maturity_rung"] == 2
    assert changes[0]["evidence"][0]["source_row"] == 2
    assert changes[0]["evidence"][0]["original_citation"]["source_id"] == "doc1"
    confirmation["qualifying_rows"][0]["grade_ppm"] = "12001"
    with pytest.raises(ValueError, match="source value"):
        corrections.overlay(base, result, confirmation)


@pytest.fixture
def public_receipt_only(tmp_path, monkeypatch):
    import shutil

    from pipeline.release_enrichment import public_check

    repository = Path(__file__).resolve().parents[1]
    name, report = public_check.STAGES["maturity"]
    receipt = load(repository / name)
    root = tmp_path / "public"
    names = {public_check.PINS, public_check.PILOT, public_check.IDENTITY, name, report} | (
        set(receipt["implementation"]) - {public_check.AUTHORITY}
    )
    for source in names:
        destination = root / source
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(repository / source, destination)
    monkeypatch.setattr(public_check, "check_identity", lambda r: load(r / public_check.IDENTITY))
    assert not (root / public_check.AUTHORITY).exists()
    return public_check, root


def test_public_ci_without_private_authority_or_release_rows(public_receipt_only):
    core, root = public_receipt_only
    receipt = core.check(root, "maturity")
    assert receipt["spot_check_agreements"] == 29
    assert not (root / "data").exists()


@pytest.mark.parametrize("mutation", ["authority", "code", "aggregate", "receipt_marker"])
def test_public_ci_rejects_changed_commitments_and_markers(public_receipt_only, mutation):
    core, root = public_receipt_only
    name, report = core.STAGES["maturity"]
    if mutation == "authority":
        pins = load(root / core.PINS)
        pins[core.AUTHORITY] = "0" * 64
        (root / core.PINS).write_text(json.dumps(pins))
    elif mutation == "code":
        (root / core.PACKAGE / "maturity.py").write_text("changed")
    elif mutation == "aggregate":
        (root / report).write_text("changed")
    else:
        receipt = load(root / name)
        receipt["spot_check_passed"] = False
        (root / name).write_text(json.dumps(receipt))
        pins = load(root / core.PINS)
        pins[name] = core.sha256(root / name)
        (root / core.PINS).write_text(json.dumps(pins))
    with pytest.raises(ValueError):
        core.check(root, "maturity")
