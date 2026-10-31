"""Returned human labels must remain bound to the exact accepted 200 pairs."""

import copy
import csv
import hashlib

import pytest

from pipeline.link.labels import HEADERS, LabelValidationError, validate_labels


@pytest.fixture
def sample():
    return {
        "sample_sha256": "a" * 64,
        "pairs": [
            {"pair_id": f"pair_{i}", "left_record_id": f"left_{i}", "right_record_id": f"right_{i}"}
            for i in range(200)
        ],
    }


def rows(sample, label="same_site"):
    return [
        {
            **pair,
            "label": label,
            "reviewer": "Human Reviewer" if label else "",
            "notes": "",
            "sample_sha256": sample["sample_sha256"],
        }
        for pair in sample["pairs"]
    ]


def write_csv(path, values, headers=HEADERS, encoding="utf-8"):
    with path.open("w", newline="", encoding=encoding) as stream:
        writer = csv.DictWriter(stream, fieldnames=headers)
        writer.writeheader()
        writer.writerows(values)
    return path


def test_complete_export_preserves_notes_order_and_all_inputs(tmp_path, sample):
    original = copy.deepcopy(sample)
    values = rows(sample)
    for i, row in enumerate(values):
        row["label"] = "same_site" if i < 80 else "different_sites"
    values[1]["notes"] = 'Evidence: "two pits", same project\nSee source notes — verified.'
    values[1]["reviewer"] = "  Named human reviewer  "
    path = write_csv(tmp_path / "labels.csv", list(reversed(values)), encoding="utf-8-sig")
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    result = validate_labels(path, sample)
    assert result["complete"]
    assert result["counts"] == {"blank": 0, "same_site": 80, "different_sites": 120, "uncertain": 0}
    assert result["rows"] == values
    assert result["row_count"] == result["decisive_count"] == 200
    assert result["unresolved_count"] == 0
    assert sample == original
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before


def test_partial_export_requires_all_rows_and_uncertain_stays_unresolved(tmp_path, sample):
    values = rows(sample, label="")
    values[0].update(label="same_site", reviewer="Reviewer")
    values[1].update(label="different_sites", reviewer="Reviewer")
    values[2].update(label="uncertain", reviewer="Reviewer", notes="Insufficient source evidence")
    path = write_csv(tmp_path / "partial.csv", values)
    result = validate_labels(path, sample, require_complete=False)
    assert not result["complete"]
    assert result["counts"] == {"blank": 197, "same_site": 1, "different_sites": 1, "uncertain": 1}
    assert result["unresolved_count"] == 198
    assert "pair_2" in result["unresolved_pair_ids"]
    with pytest.raises(LabelValidationError, match="197 blank and 1 uncertain"):
        validate_labels(path, sample)
    path = write_csv(path, values[:-1])
    with pytest.raises(LabelValidationError, match="missing 1"):
        validate_labels(path, sample, require_complete=False)


@pytest.mark.parametrize(
    "change,message",
    [
        ({"sample_sha256": "b" * 64}, "sample_sha256"),
        ({"sample_sha256": "invalid"}, "sample_sha256"),
        ({"left_record_id": "changed"}, "Changed record IDs"),
        ({"right_record_id": "changed"}, "Changed record IDs"),
        ({"pair_id": "new-pair"}, "Unexpected review pair_id"),
        ({"label": "yes"}, "Unsupported label"),
        ({"label": "same_site "}, "Unsupported label"),
        ({"label": "same_site", "reviewer": " \t"}, "reviewer is required"),
        ({"label": "uncertain", "reviewer": ""}, "reviewer is required"),
    ],
)
def test_altered_stale_or_unsupported_rows_rejected_in_partial_mode(
    tmp_path,
    sample,
    change,
    message,
):
    values = rows(sample)
    values[4].update(change)
    path = write_csv(tmp_path / "changed.csv", values)
    with pytest.raises(LabelValidationError, match=message):
        validate_labels(path, sample, require_complete=False)


def test_swapped_pair_sides_and_duplicate_rows_are_not_silently_repaired(tmp_path, sample):
    values = rows(sample)
    values[0]["left_record_id"], values[0]["right_record_id"] = (
        values[0]["right_record_id"],
        values[0]["left_record_id"],
    )
    path = write_csv(tmp_path / "changed.csv", values)
    with pytest.raises(LabelValidationError, match="Changed record IDs"):
        validate_labels(path, sample)
    values = rows(sample)
    values[-1] = dict(values[0])
    write_csv(path, values)
    with pytest.raises(LabelValidationError, match="Duplicate review pair_id"):
        validate_labels(path, sample)
    write_csv(path, [*rows(sample), rows(sample)[0]])
    with pytest.raises(LabelValidationError, match="Duplicate review pair_id"):
        validate_labels(path, sample)


@pytest.mark.parametrize(
    "headers",
    [
        (*HEADERS[:-1], "digest"),
        tuple(reversed(HEADERS)),
        (*HEADERS, "extra"),
        (HEADERS[0], HEADERS[0], *HEADERS[2:]),
    ],
)
def test_headers_must_match_review_page_exactly(tmp_path, sample, headers):
    path = tmp_path / "wrong-headers.csv"
    path.write_text(",".join(headers) + "\n")
    with pytest.raises(LabelValidationError, match="headers must exactly match"):
        validate_labels(path, sample, require_complete=False)


@pytest.mark.parametrize(
    "body",
    [
        "pair_0,left_0,right_0,same_site,Reviewer,notes\n",
        "pair_0,left_0,right_0,same_site,Reviewer,notes," + "a" * 64 + ",extra\n",
        'pair_0,left_0,right_0,same_site,Reviewer,"unclosed\n',
    ],
)
def test_malformed_csv_is_rejected(tmp_path, sample, body):
    path = tmp_path / "malformed.csv"
    path.write_text(",".join(HEADERS) + "\n" + body)
    with pytest.raises(LabelValidationError):
        validate_labels(path, sample, require_complete=False)


def test_sample_itself_requires_exactly_200_unique_pairs_and_valid_sha(tmp_path, sample):
    path = write_csv(tmp_path / "labels.csv", rows(sample))
    for sha in (None, "", "not-a-digest", "g" * 64, "a" * 63):
        changed = {**sample, "sample_sha256": sha}
        with pytest.raises(LabelValidationError, match="SHA-256"):
            validate_labels(path, changed)
    for pairs in (sample["pairs"][:-1], [*sample["pairs"], sample["pairs"][0]]):
        with pytest.raises(LabelValidationError, match="exactly 200"):
            validate_labels(path, {**sample, "pairs": pairs})
    changed = copy.deepcopy(sample)
    changed["pairs"][-1] = dict(changed["pairs"][0])
    with pytest.raises(LabelValidationError, match="duplicate"):
        validate_labels(path, changed)
    changed["pairs"][-1]["pair_id"] = "another_id_for_same_pair"
    with pytest.raises(LabelValidationError, match="duplicate pairs"):
        validate_labels(path, changed)


def test_all_uncertain_is_not_a_completed_gold_set(tmp_path, sample):
    path = write_csv(tmp_path / "uncertain.csv", rows(sample, "uncertain"))
    result = validate_labels(path, sample, require_complete=False)
    assert result["unresolved_count"] == 200 and result["decisive_count"] == 0
    with pytest.raises(LabelValidationError, match="200 uncertain"):
        validate_labels(path, sample)
