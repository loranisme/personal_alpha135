import pandas as pd

from sec_edgar_pipeline.submissions import build_accession_index


def test_recent_and_older_submission_rows_are_flattened():
    submissions = {
        "filings": {
            "recent": {
                "accessionNumber": ["0001-24-000001"],
                "filingDate": ["2024-02-01"],
                "acceptanceDateTime": ["20240201170000"],
                "form": ["10-K"],
                "reportDate": ["2023-12-31"],
            }
        }
    }
    older = [{
        "accessionNumber": ["0001-23-000001"],
        "filingDate": ["2023-02-01"],
        "acceptanceDateTime": ["20230201170000"],
        "form": ["10-K"],
        "reportDate": ["2022-12-31"],
    }]
    result = build_accession_index(submissions, older)
    assert result.accession_number.tolist() == ["0001-23-000001", "0001-24-000001"]
    assert result.acceptance_status.eq("EXACT").all()


def test_conflicting_acceptance_times_are_retained_as_unresolved():
    recent = {
        "accessionNumber": ["0001-24-000001", "0001-24-000001"],
        "filingDate": ["2024-02-01", "2024-02-01"],
        "acceptanceDateTime": ["20240201170000", "20240201180000"],
        "form": ["10-K", "10-K"],
        "reportDate": ["2023-12-31", "2023-12-31"],
    }
    result = build_accession_index({"filings": {"recent": recent}}, [])
    assert len(result) == 1
    row = result.iloc[0]
    assert row.acceptance_status == "UNRESOLVED_ACCEPTANCE_CONFLICT"
    assert pd.isna(row.acceptance_datetime)
