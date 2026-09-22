import pandas as pd

from sec_edgar_pipeline.normalize import normalize_companyfacts
from sec_edgar_pipeline.submissions import build_accession_index


def _submissions():
    return {
        "filings": {
            "recent": {
                "accessionNumber": ["0001-24-000001", "0001-24-000099"],
                "filingDate": ["2024-02-01", "2024-03-15"],
                "acceptanceDateTime": ["20240201170000", "20240315170000"],
                "form": ["10-K", "10-K/A"],
                "reportDate": ["2023-12-31", "2023-12-31"],
            }
        }
    }


def _companyfacts():
    common = {
        "start": "2023-01-01",
        "end": "2023-12-31",
        "fy": 2023,
        "fp": "FY",
        "form": "10-K",
        "filed": "2024-02-01",
        "frame": "CY2023",
    }
    amendment = dict(common) | {
        "val": 110,
        "accn": "0001-24-000099",
        "form": "10-K/A",
        "filed": "2024-03-15",
    }
    return {
        "cik": 1,
        "entityName": "Fixture Co",
        "facts": {
            "us-gaap": {
                "Revenues": {
                    "label": "Revenue",
                    "description": "Revenue",
                    "units": {"USD": [
                        dict(common) | {"val": 100, "accn": "0001-24-000001"},
                        amendment,
                    ]},
                }
            },
            "fixture-custom": {
                "AdjustedRevenue": {
                    "label": "Adjusted revenue",
                    "description": "Custom extension",
                    "units": {"USD": [dict(common) | {"val": 999, "accn": "0001-24-000001"}]},
                }
            },
        },
    }


def test_amendment_does_not_overwrite_original():
    result = normalize_companyfacts(
        _companyfacts(),
        build_accession_index(_submissions(), []),
        {
            "issuer_cik": "0000000001",
            "security_id": "sec-1",
            "ticker": "FIX",
        },
        "a" * 64,
        pd.Timestamp("2024-03-16T00:00:00Z"),
        "1",
    )
    rows = result.query(
        "xbrl_tag == 'Revenues' and accession_number in ['0001-24-000001', '0001-24-000099']"
    ).sort_values("historical_available_at")
    assert len(rows) == 2
    assert rows.amendment_flag.tolist() == [False, True]
    assert rows.source_fact_id.nunique() == 2
    assert rows.historical_available_at.is_monotonic_increasing
    assert rows.duration_days.tolist() == [365, 365]


def test_custom_namespace_is_retained_but_deferred():
    result = normalize_companyfacts(
        _companyfacts(),
        build_accession_index(_submissions(), []),
        {"issuer_cik": "0000000001", "security_id": "sec-1", "ticker": "FIX"},
        "a" * 64,
        pd.Timestamp("2024-03-16T00:00:00Z"),
        "1",
    )
    custom = result.query("xbrl_namespace == 'fixture-custom'").iloc[0]
    assert custom.mapping_status == "DEFERRED_CUSTOM_TAG"
    assert custom.canonical_field is None


def test_unsupported_dimensions_are_retained_but_deferred():
    payload = _companyfacts()
    payload["facts"]["us-gaap"]["Revenues"]["units"]["USD"][0]["dimensions"] = {
        "segment": "Example"
    }
    result = normalize_companyfacts(
        payload,
        build_accession_index(_submissions(), []),
        {"issuer_cik": "0000000001", "security_id": "sec-1", "ticker": "FIX"},
        "a" * 64,
        pd.Timestamp("2024-03-16T00:00:00Z"),
        "1",
    )
    row = result.query("xbrl_tag == 'Revenues' and accession_number == '0001-24-000001'").iloc[0]
    assert row.mapping_status == "DEFERRED_DIMENSION"
    assert row.dimensions_signature


def test_conflicting_acceptance_is_preserved_and_never_made_date_only():
    conflicting = _submissions()
    recent = conflicting["filings"]["recent"]
    for key in list(recent):
        recent[key] = [recent[key][0], recent[key][0]]
    recent["acceptanceDateTime"] = ["20240201170000", "20240201180000"]

    result = normalize_companyfacts(
        _companyfacts(),
        build_accession_index(conflicting, []),
        {"issuer_cik": "0000000001", "security_id": "sec-1", "ticker": "FIX"},
        "a" * 64,
        pd.Timestamp("2024-03-16T00:00:00Z"),
        "1",
    )
    row = result.query(
        "xbrl_tag == 'Revenues' and accession_number == '0001-24-000001'"
    ).iloc[0]
    assert row.acceptance_status == "UNRESOLVED_ACCEPTANCE_CONFLICT"
    assert row.availability_precision == "UNRESOLVED_ACCEPTANCE_CONFLICT"
    assert pd.isna(row.historical_available_at)
    assert pd.isna(row.available_at)
