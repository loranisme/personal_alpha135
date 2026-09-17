import json
import os
import shutil
import subprocess
from pathlib import Path

import openpyxl
import pandas as pd


NODE = Path(shutil.which("node") or "node")


def _completed_filter_run_fixture(tmp_path: Path) -> Path:
    run = tmp_path / "run"
    run.mkdir()
    pd.DataFrame(
        [
            {
                "factor_id": "F1",
                "factor_version": 4,
                "family_id": "family-1",
                "mechanism_tag": "momentum",
                "valid_signal_sessions": 120,
                "median_cross_section_coverage": 0.99,
                "mean_rank_ic_5d": 0.02,
                "icir_5d": 0.30,
                "positive_year_ratio": 0.75,
                "q1_mean_5d": -0.01,
                "q5_mean_5d": 0.01,
                "q5_minus_q1_5d": 0.02,
                "weekly_top10_turnover": 0.40,
                "filter_status": "SELECTED",
                "filter_reason": "SELECTED_NONREDUNDANT",
            }
        ]
    ).to_csv(run / "alpha_diagnostics.csv", index=False)
    pd.DataFrame(
        [
            {
                "factor_id": "F1",
                "factor_version": 4,
                "filter_status": "SELECTED",
                "filter_reason": "SELECTED_NONREDUNDANT",
            }
        ]
    ).to_csv(run / "filter_decisions.csv", index=False)
    pd.DataFrame(
        [
            {
                "composite_id": "V5_1_PROPOSED",
                "comparison_status": "NOT_RUN",
                "missing_reason": "EXPLICIT_FROZEN_BASELINE_COMPARISON_REQUIRED",
                "selection_evidence": "DEVELOPMENT_EXPOSED",
            },
            {
                "composite_id": "BASELINE_V5_8_FACTOR",
                "comparison_status": "NOT_RUN",
                "missing_reason": "EXPLICIT_FROZEN_BASELINE_COMPARISON_REQUIRED",
                "selection_evidence": "DEVELOPMENT_EXPOSED",
            },
        ]
    ).to_csv(run / "composite_comparison.csv", index=False)
    pd.DataFrame(
        [{"factor_id": "F2", "filter_status": "REJECT_SIGNAL", "filter_reason": "NONPOSITIVE_MEAN_RANK_IC"}]
    ).to_csv(run / "blocked_items.csv", index=False)
    (run / "proposed_active_pool.json").write_text(
        json.dumps(
            {
                "filter_run_id": "run-1",
                "status": "BLOCKED",
                "factor_count": 1,
                "selected_factors": [{"factor_id": "F1", "factor_version": 4}],
                "blockers": ["COMPOSITE_BLOCKED_INSUFFICIENT_ALPHA"],
                "provider": "tiingo_eod",
                "sample_start": "2020-01-01",
                "sample_end": "2025-12-31",
                "selection_evidence": "DEVELOPMENT_EXPOSED",
                "historical_pit_verified": False,
                "survivorship_bias": True,
                "live_orders_submitted": 0,
            }
        ),
        encoding="utf-8",
    )
    (run / "run_status.json").write_text(
        json.dumps(
            {
                "status": "BLOCKED",
                "catalog_factor_version_count": 2,
                "selected_factor_count": 1,
                "selection_evidence": "DEVELOPMENT_EXPOSED",
                "historical_pit_verified": False,
                "live_orders_submitted": 0,
            }
        ),
        encoding="utf-8",
    )
    (run / "filter_config.json").write_text(
        json.dumps(
            {
                "primary_frequency": "WEEKLY_REBALANCE_SIGNAL",
                "forward_label": "T_CLOSE_T1_OPEN_T6_OPEN",
            }
        ),
        encoding="utf-8",
    )
    (run / "manifest.json").write_text(
        json.dumps(
            {
                "filter_run_id": "run-1",
                "selection_evidence": "DEVELOPMENT_EXPOSED",
                "historical_pit_verified": False,
                "survivorship_bias": True,
                "live_orders_submitted": 0,
                "files": {},
            }
        ),
        encoding="utf-8",
    )
    return run


def test_report_contains_only_required_review_sheets(tmp_path):
    run_dir = _completed_filter_run_fixture(tmp_path)
    output = tmp_path / "alpha_filter_review.xlsx"
    previews = tmp_path / "previews"
    subprocess.run(
        [
            str(NODE),
            "scripts/build_v5_1_alpha_filter_workbook.mjs",
            str(run_dir),
            str(output),
            str(previews),
        ],
        check=True,
        env=os.environ.copy(),
    )
    workbook = openpyxl.load_workbook(output, data_only=False)
    assert workbook.sheetnames == [
        "Summary",
        "Alpha Diagnostics",
        "Selected Pool",
        "Composite Comparison",
        "Audit",
    ]
    audit = workbook["Audit"]
    text = " ".join(
        str(cell.value) for row in audit.iter_rows() for cell in row if cell.value
    )
    for required in [
        "DEVELOPMENT_EXPOSED",
        "historical_pit_verified=false",
        "t close -> t+1 open -> t+6 open",
        "live_orders_submitted=0",
    ]:
        assert required in text
    assert len(list(previews.glob("*.png"))) == 5
    formula_errors = {
        "#REF!", "#DIV/0!", "#VALUE!", "#NAME?", "#N/A", "#NUM!", "#NULL!"
    }
    assert not any(
        cell.value in formula_errors
        for sheet in workbook.worksheets
        for row in sheet.iter_rows()
        for cell in row
    )
