"""Report categories preserve group distinctions and avoid invented ISO codes."""

import csv
import io
from types import SimpleNamespace

from pipeline.pilot.reporting import generate


def test_multiple_associations_do_not_become_additive_unique_sites():
    source = {
        "source_id": "fixture",
        "input_rows": 2,
        "kept_rows": 1,
        "excluded_rows": 1,
        "qualified_pilot_rows": 1,
        "unresolved_country_rows": 1,
        "counts": {
            "commodity": {"REE": 1, "Nd": 1},
            "family": {"REE": 1},
            "host_country": {"UNRESOLVED": 1},
            "record_kind": {"deposit": 1},
        },
    }
    vocab = SimpleNamespace(
        entries={
            "commodities": {"REE": {"name": "Rare earth elements"}, "Nd": {"name": "Neodymium"}}
        }
    )
    outputs = generate([source], vocab)
    commodities = list(
        csv.DictReader(io.StringIO(outputs["reports/pilot-counts-by-commodity.csv"].decode()))
    )
    assert len(commodities) == 3
    assert [r["records"] for r in commodities if r["dimension"] == "family"] == ["1"]
    countries = list(
        csv.DictReader(io.StringIO(outputs["reports/pilot-counts-by-country.csv"].decode()))
    )
    assert countries[0]["country_iso3"] == ""
    assert countries[0]["country_name"] == "Unassigned ISO country"
    assert outputs == generate([source], vocab)
