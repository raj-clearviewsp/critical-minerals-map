"""Readable counts of source records; categories overlap and are not unique sites."""

import csv
import io

import pycountry


def _csv(fields, rows):
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=fields, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return stream.getvalue().encode("utf-8")


def generate(results, vocab):
    source_rows, commodity_rows, country_rows, kind_rows = [], [], [], []
    family_names = {
        "Ni": "Nickel",
        "Co": "Cobalt",
        "REE": "Rare earth elements (aggregate or member)",
    }
    for source in sorted(results, key=lambda s: s["source_id"]):
        source_rows.append(
            {
                k: source[k]
                for k in (
                    "source_id",
                    "input_rows",
                    "kept_rows",
                    "excluded_rows",
                    "qualified_pilot_rows",
                    "unresolved_country_rows",
                )
            }
            | {"unassigned_country_rows": source["counts"]["host_country"].get("UNRESOLVED", 0)}
        )
        for dimension in ("commodity", "family"):
            for code, count in sorted(source["counts"][dimension].items()):
                name = (
                    family_names[code]
                    if dimension == "family"
                    else vocab.entries["commodities"][code]["name"]
                )
                commodity_rows.append(
                    {
                        "source_id": source["source_id"],
                        "dimension": dimension,
                        "code": code,
                        "name": name,
                        "records": count,
                    }
                )
        for code, count in sorted(source["counts"]["host_country"].items()):
            country = pycountry.countries.get(alpha_3=code)
            if country is None and code != "UNRESOLVED":
                raise ValueError("Unknown country count category")
            country_rows.append(
                {
                    "source_id": source["source_id"],
                    "country_iso3": code if country else "",
                    "country_name": country.name if country else "Unassigned ISO country",
                    "records": count,
                }
            )
        for kind, count in sorted(source["counts"]["record_kind"].items()):
            kind_rows.append(
                {"source_id": source["source_id"], "record_kind": kind, "records": count}
            )
    return {
        "reports/pilot-counts-by-source.csv": _csv(
            [
                "source_id",
                "input_rows",
                "kept_rows",
                "excluded_rows",
                "qualified_pilot_rows",
                "unresolved_country_rows",
                "unassigned_country_rows",
            ],
            source_rows,
        ),
        "reports/pilot-counts-by-commodity.csv": _csv(
            ["source_id", "dimension", "code", "name", "records"], commodity_rows
        ),
        "reports/pilot-counts-by-country.csv": _csv(
            ["source_id", "country_iso3", "country_name", "records"], country_rows
        ),
        "reports/pilot-counts-by-kind.csv": _csv(
            ["source_id", "record_kind", "records"], kind_rows
        ),
    }
