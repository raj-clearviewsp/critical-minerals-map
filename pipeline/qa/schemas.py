"""Provisional Phase 0 table contracts for the Step 1 empty-input scaffold.

Lists and geometry are serialized strings here. Production GeoParquet contracts,
controlled vocabularies, foreign keys and field evidence arrive in later steps.
These contracts deliberately accept zero rows only; they cannot validate real data.
"""

import pandas as pd
import pandera.pandas as pa

# key: pandas nullable dtype. No mineral facts or inferred provenance defaults.
TABLE_FIELDS = {
    "source": {
        "source_id": "string",
        "name": "string",
        "publisher": "string",
        "url": "string",
        "license": "string",
        "redistribution_ok": "boolean",
        "currency_date": "string",
        "retrieved_at": "string",
        "checksum": "string",
    },
    "source_record": {
        "record_id": "string",
        "source_id": "string",
        "raw_name": "string",
        "aliases": "string",
        "lat": "Float64",
        "lon": "Float64",
        "location_precision": "string",
        "country": "string",
        "raw_commodities": "string",
        "raw_deposit_type": "string",
        "raw_status": "string",
        "raw_size_grade_text": "string",
        "reference": "string",
        "source_url": "string",
        "retrieved_at": "string",
        "evidence_grade": "string",
    },
    "site": {
        "site_id": "string",
        "name": "string",
        "alt_names": "string",
        "geometry": "string",
        "location_precision_m": "Float64",
        "country_iso3": "string",
        "admin1": "string",
        "primary_commodities": "string",
        "byproduct_commodities": "string",
        "deposit_type": "string",
        "maturity_rung": "Int64",
        "reporting_system": "string",
        "status": "string",
        "review_state": "string",
        "maturity_evidence_record_id": "string",
        "source_url": "string",
        "retrieved_at": "string",
        "evidence_grade": "string",
    },
    "site_member": {
        "site_id": "string",
        "record_id": "string",
        "match_score": "Float64",
        "method": "string",
    },
    "facility": {
        "facility_id": "string",
        "site_id": "string",
        "facility_type": "string",
        "commodities": "string",
        "capacity": "Float64",
        "capacity_unit": "string",
        "status": "string",
        "operator_name": "string",
        "source_url": "string",
        "retrieved_at": "string",
        "evidence_grade": "string",
    },
    "production": {
        "entity_ref": "string",
        "entity_type": "string",
        "commodity": "string",
        "year": "Int64",
        "quantity": "Float64",
        "unit": "string",
        "source_id": "string",
        "source_url": "string",
        "retrieved_at": "string",
        "evidence_grade": "string",
    },
    "country_commodity": {
        "country_iso3": "string",
        "commodity": "string",
        "year": "Int64",
        "production": "Float64",
        "reserves": "Float64",
        "unit": "string",
        "world_share": "Float64",
        "source_id": "string",
        "source_url": "string",
        "retrieved_at": "string",
        "evidence_grade": "string",
    },
    "export_restriction": {
        "country_iso3": "string",
        "commodity": "string",
        "measure_type": "string",
        "start_date": "string",
        "end_date": "string",
        "source_id": "string",
        "source_url": "string",
        "retrieved_at": "string",
        "evidence_grade": "string",
    },
    "china_official_finance": {
        "aiddata_project_id": "string",
        "country_iso3": "string",
        "amount": "Float64",
        "currency": "string",
        "year": "Int64",
        "lender": "string",
        "site_id": "string",
        "source_id": "string",
        "source_url": "string",
        "retrieved_at": "string",
        "evidence_grade": "string",
    },
}


def empty_table(name: str) -> pd.DataFrame:
    return pd.DataFrame(
        {column: pd.Series(dtype=dtype) for column, dtype in TABLE_FIELDS[name].items()}
    )


def validate_empty_table(name: str, frame: pd.DataFrame) -> pd.DataFrame:
    """Fail closed on real rows until subsequent steps implement ingestion and QA."""
    schema = pa.DataFrameSchema(
        {column: pa.Column(dtype, nullable=True) for column, dtype in TABLE_FIELDS[name].items()},
        strict=True,
        ordered=True,
        checks=pa.Check(lambda df: len(df) == 0, error="Step 1 permits empty tables only"),
        name=f"scaffold_{name}",
    )
    return schema.validate(frame, lazy=True)
