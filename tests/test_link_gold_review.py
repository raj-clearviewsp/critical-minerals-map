"""Review packets bind blank human labels to unchanged source evidence and receipts."""

import copy
import csv
import io
import json
import sys

import pyarrow as arrow
import pyarrow.parquet as parquet
import pytest

from pipeline.link import __main__ as cli
from pipeline.link import gold_review as review
from pipeline.link.labels import LabelValidationError
from pipeline.link.scope import review_eligibility
from pipeline.normalize.core import NormalizeError, sha256
from pipeline.scaffold import json_bytes

SOURCE_IDS = (
    "aiddata_transition_v2",
    "ga_critical_2025",
    "ga_critical_2025_arcgis",
    "ga_global_ree",
    "gseu_crm",
    "icmm",
    "jasansky",
    "oecd_export",
    "usgs_global_critical",
    "usgs_global_ree",
    "usgs_laterites",
    "usgs_mcs_2026",
    "usgs_mrds",
    "usgs_us_critical",
    "wapha",
)
PRIVATE_NAME = "PRIVATE RAW NAME MUST NOT APPEAR IN THE COMMITTED RECEIPT"


def staged_row(source, number=0, *, kind="deposit"):
    row = {
        "record_id": f"record_{source}_{number}",
        "source_id": source,
        "native_id": f"native_{number}",
        "raw_name": f"{PRIVATE_NAME} {number}",
        "record_kind": kind,
        "country": "Source country text",
        "location_precision": None,
        "lat": 12.0,
        "lon": 23.0,
        "source_url": "https://example.test/source",
        "source_file": f"{source}/2026-10-31/original.csv",
        "source_table": "original",
        "source_row": number + 1,
        "retrieved_at": None,
        "registered_at": "2026-10-31T00:00:00Z",
        "raw_sha256": "a" * 64,
        "source_currency": "2019",
        "evidence_grade": "B",
        "aliases": json.dumps(["Unsplit; Original Alias"]),
        "country_iso3s": '["AUS"]',
        "pilot_families": '["Ni"]',
        "pilot_commodities": '["Ni"]',
        "pilot_evidence": "[]",
        "raw_fields": "{}",
        "raw_commodities": '{"Commods":"Ni"}',
        "raw_status": "{}",
        "raw_deposit_type": "{}",
        "raw_geometry": "{}",
        "reference": '{"original":"Original citation"}',
    }
    return row


@pytest.fixture
def checkpoint(tmp_path, monkeypatch):
    """Keep real Parquet, packet generation and validation; stub only prior stages."""
    (tmp_path / "data/staging/pilot").mkdir(parents=True)
    (tmp_path / "reports").mkdir()
    (tmp_path / "sources").mkdir()
    (tmp_path / "pipeline/link").mkdir(parents=True)
    (tmp_path / "pipeline/link/__init__.py").write_text("# Synthetic implementation fingerprint\n")
    (tmp_path / "uv.lock").write_text("synthetic locked dependencies\n")
    parents = []
    for source in SOURCE_IDS:
        # 21 colocated deposits give 210 distinct pairs: a real 200-pair sample.
        rows = (
            [staged_row(source, number) for number in range(21)]
            if source == "usgs_global_ree"
            else [staged_row(source, kind="source_group")]
        )
        relative = f"data/staging/pilot/{source}.parquet"
        parquet.write_table(arrow.Table.from_pylist(rows), tmp_path / relative)
        parents.append(
            {
                "source_id": source,
                "artifact": relative,
                "sha256": sha256(tmp_path / relative),
                "kept_rows": len(rows),
            }
        )
        (tmp_path / "sources" / f"{source}.yaml").write_text(
            json.dumps({"source_id": source, "name": f"Publisher source {source}"})
        )
    parent = {"kept_rows": sum(s["kept_rows"] for s in parents), "sources": parents}
    (tmp_path / "reports/pilot-audit.json").write_bytes(json_bytes(parent))
    calls = []

    def prior_check(root, *, raw=False):
        assert root == tmp_path
        calls.append(raw)
        return copy.deepcopy(parent)

    monkeypatch.setattr(review, "check_pilot", prior_check)
    receipt = review.prepare(tmp_path)
    return tmp_path, parent, receipt, calls


def test_full_packet_has_200_blank_pairs_and_private_details_only_in_local_artifacts(checkpoint):
    root, parent, receipt, calls = checkpoint
    assert calls == [True]
    assert len(receipt["sources"]) == 15
    assert receipt["input_rows"] == parent["kept_rows"] == 35
    assert receipt["eligible_rows"] == 21 and receipt["excluded_rows"] == 14
    assert receipt["candidate_pairs"] == 210 and receipt["sample_size"] == 200
    receipt_bytes = (root / review.RECEIPT).read_bytes()
    assert PRIVATE_NAME.encode() not in receipt_bytes
    assert b"Unsplit; Original Alias" not in receipt_bytes
    sample = json.loads((root / review.DIRECTORY / "sample.json").read_text())
    assert len(sample["pairs"]) == 200
    assert sample["sample_sha256"] == review.sample_digest(sample) == receipt["sample_sha256"]
    assert all(
        pair["label"] == pair["reviewer"] == pair["review_notes"] == "" for pair in sample["pairs"]
    )
    assert all(PRIVATE_NAME in pair["left"]["name"] for pair in sample["pairs"])
    rows = list(
        csv.DictReader(io.StringIO((root / review.DIRECTORY / "labels-blank.csv").read_text()))
    )
    assert len(rows) == len({row["pair_id"] for row in rows}) == 200
    assert all(row["label"] == row["reviewer"] == row["notes"] == "" for row in rows)
    assert all(row["sample_sha256"] == sample["sample_sha256"] for row in rows)
    assert not (root / "data/canonical").exists()
    assert not (root / "data/publish").exists()


def test_repeat_prepare_and_raw_check_preserve_every_accepted_byte_and_mtime(checkpoint):
    root, parent, receipt, _ = checkpoint
    paths = [root / s["artifact"] for s in parent["sources"]]
    paths += [root / path for path in [*review.ARTIFACTS, review.RECEIPT]]
    before = {path: (sha256(path), path.stat().st_mtime_ns) for path in paths}
    assert review.prepare(root) == receipt
    assert review.check(root, raw=True) == receipt
    assert {path: (sha256(path), path.stat().st_mtime_ns) for path in paths} == before


def test_only_icmm_semicolon_aliases_split_and_original_cells_survive():
    for source in ("icmm", "usgs_global_ree", "jasansky"):
        row = staged_row(source)
        row["aliases"] = json.dumps([" Beta ; Alpha; Beta ", "Gamma, Delta"])
        original = copy.deepcopy(row)
        detail = review.record_detail(row, review_eligibility(row))
        assert detail["aliases_raw"] == json.loads(original["aliases"])
        assert detail["aliases"] == (
            ["Alpha", "Beta", "Gamma, Delta"] if source == "icmm" else json.loads(row["aliases"])
        )
        assert row == original


def test_packet_is_independent_of_asset_and_detail_dictionary_order(checkpoint):
    root, parent, _, _ = checkpoint
    assets, details, sources = review.load_assets(root, parent)
    before = copy.deepcopy((assets, details))
    names = review.source_names(root, sources)
    expected = review.packet(assets, details, names)
    actual = review.packet(list(reversed(assets)), dict(reversed(list(details.items()))), names)
    assert actual == expected
    assert (assets, details) == before


@pytest.mark.parametrize(
    "change",
    [
        lambda r: r["sources"].pop(),
        lambda r: r["sources"].__setitem__(0, copy.deepcopy(r["sources"][1])),
        lambda r: r["sources"][0].__setitem__("input_sha256", "b" * 64),
        lambda r: r["sources"][0].__setitem__("input_artifact", "../outside.parquet"),
        lambda r: r["sources"][0].__setitem__("input_rows", 99),
        lambda r: r["sources"][0].__setitem__("exclusion_reasons", {}),
        lambda r: r.__setitem__("eligible_rows", r["eligible_rows"] + 1),
        lambda r: r.__setitem__("model_fitted", True),
        lambda r: r["sampling"].__setitem__("label_status", "review_complete"),
        lambda r: r["sampling"]["strata"][0].__setitem__("sample_weight", 999),
        lambda r: r["sampling"]["coverage"]["source"].__setitem__("sampled", []),
        lambda r: r["sampling"]["coverage"]["family"].__setitem__("unrepresented", ["Ni"]),
        lambda r: r["sampling"]["strata"][0].__setitem__("stratum_id", "stratum_" + "a" * 64),
        lambda r: r["sampling"]["strata"][0].__setitem__("shared_families", ["Cu"]),
        lambda r: r["sampling"]["strata"][0].__setitem__("distance_band", "outside_block"),
        lambda r: r.__setitem__("sample_sha256", "G" * 64),
        lambda r: r.__setitem__("sample_sha256", "A" * 64),
        lambda r: r["artifacts"][f"{review.DIRECTORY}/sample.json"].__setitem__("sha256", "z" * 64),
        lambda r: r["artifacts"].__setitem__("../outside.json", {"sha256": "a" * 64, "bytes": 1}),
    ],
)
def test_receipt_rejects_parent_reconciliation_and_sampling_tampering(checkpoint, change):
    _, parent, receipt, _ = checkpoint
    altered = copy.deepcopy(receipt)
    change(altered)
    with pytest.raises(review.ReviewError):
        review.validate_receipt(altered, parent)


def test_raw_check_detects_local_artifact_changes_and_prepare_refuses_overwrite(checkpoint):
    root, _, _, _ = checkpoint
    path = root / review.DIRECTORY / "labels-blank.csv"
    path.write_bytes(path.read_bytes() + b"altered\n")
    changed = path.read_bytes()
    with pytest.raises(review.ReviewError, match="Local review artifact changed"):
        review.check(root, raw=True)
    with pytest.raises(NormalizeError, match="Existing staging artifact differs"):
        review.prepare(root)
    assert path.read_bytes() == changed


def test_check_detects_parent_receipt_and_implementation_changes(checkpoint):
    root, _, _, _ = checkpoint
    path = root / "reports/pilot-audit.json"
    original = path.read_bytes()
    path.write_bytes(original + b" ")
    with pytest.raises(review.ReviewError, match="Pilot receipt changed"):
        review.check(root)
    path.write_bytes(original)
    (root / "pipeline/link/__init__.py").write_text("# Changed implementation\n")
    with pytest.raises(review.ReviewError, match="implementation changed"):
        review.check(root)


def test_cli_partial_validation_never_completes_or_mutates_blank_packet(
    checkpoint, monkeypatch, capsys
):
    root, _, _, _ = checkpoint
    sample = root / review.DIRECTORY / "sample.json"
    before = sample.read_bytes()
    csv_path = root / review.DIRECTORY / "labels-blank.csv"
    monkeypatch.setattr(
        sys, "argv", ["link", "--root", str(root), "--labels", str(csv_path), "--partial"]
    )
    assert cli.main() == 0
    output = json.loads(capsys.readouterr().out)
    assert output["complete"] is False and output["unresolved_count"] == 200
    assert "rows" not in output and PRIVATE_NAME not in json.dumps(output)
    assert sample.read_bytes() == before
    monkeypatch.setattr(sys, "argv", ["link", "--root", str(root), "--labels", str(csv_path)])
    with pytest.raises(LabelValidationError, match="incomplete"):
        cli.main()


def test_cli_rejects_modified_embedded_sample_hash_even_when_content_digest_is_unchanged(
    checkpoint, monkeypatch
):
    root, _, receipt, _ = checkpoint
    sample_path = root / review.DIRECTORY / "sample.json"
    sample = json.loads(sample_path.read_text())
    sample["sample_sha256"] = "b" * 64
    assert review.sample_digest(sample) == receipt["sample_sha256"]
    sample_path.write_bytes(json_bytes(sample))
    altered_labels = root / "altered-labels.csv"
    altered_labels.write_bytes(review.label_csv(sample["pairs"], sample["sample_sha256"]))
    monkeypatch.setattr(
        sys, "argv", ["link", "--root", str(root), "--labels", str(altered_labels), "--partial"]
    )
    with pytest.raises((SystemExit, review.ReviewError)):
        cli.main()


@pytest.mark.parametrize("flags", [["--raw"], ["--partial"], ["--labels", "labels.csv", "--check"]])
def test_cli_invalid_action_combinations_fail_before_processing(monkeypatch, flags):
    monkeypatch.setattr(sys, "argv", ["link", *flags])
    with pytest.raises(SystemExit) as error:
        cli.main()
    assert error.value.code == 2
