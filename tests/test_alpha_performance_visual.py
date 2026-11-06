import json

import numpy as np
import pandas as pd

from us_equity_alpha.performance_visual import build_decile_return_table, render_performance_report


def test_decile_returns_use_signal_order_and_leave_missing_labels_missing():
    dates = pd.to_datetime(["2024-12-27", "2025-01-03"], utc=True)
    names = [f"S{i:02}" for i in range(20)]
    scores = pd.DataFrame([range(20), range(20)], index=dates, columns=names)
    forward = pd.DataFrame([np.arange(20) / 100, [np.nan] * 20], index=dates, columns=names)
    result = build_decile_return_table(scores, forward)
    assert len(result) == 2
    assert result.loc[dates[0], "G1"] == 0.005
    assert result.loc[dates[0], "G10"] == 0.185
    assert np.isnan(result.loc[dates[1], "G1"])
    assert result.loc[dates[1], "valid_forward_count"] == 0


def test_report_renders_only_available_horizon_and_cost(tmp_path):
    (tmp_path / "alphalens").mkdir()
    (tmp_path / "vectorbt").mkdir()
    dates = pd.to_datetime(["2024-12-20", "2024-12-27", "2025-01-03", "2025-01-10"], utc=True)
    frame = pd.DataFrame({f"G{i}": [0.01 * i] * 4 for i in range(1, 11)}, index=dates)
    frame["valid_forward_count"] = 100
    frame.to_csv(tmp_path / "alphalens/decile_5d_returns.csv", index_label="signal_session")
    pd.DataFrame({"5D": [0.01, 0.02, -0.01, 0.03]}, index=dates).to_csv(tmp_path / "alphalens/ic_by_date.csv", index_label="date")
    pd.DataFrame({"portfolio_index": [1, 1.01, 1.02, 1.03], "spy_index": [1, 1.02, 1.03, 1.04], "universe_equal_weight_index": [1, 1.01, 1.01, 1.02]}, index=dates).to_csv(tmp_path / "vectorbt/nav_and_benchmarks.csv", index_label="session")
    (tmp_path / "run_summary.json").write_text(json.dumps({"factor_id": "alpha-1", "formula": "rank(close)", "data_source": "saved_yfinance", "vectorbt": {"fee_rate_one_way": 0.001}, "forward_label": "T_CLOSE_T1_OPEN_T6_OPEN"}))
    result = render_performance_report(tmp_path)
    assert result["status"] == "PASS"
    assert (tmp_path / "performance_report.png").stat().st_size > 1000
    report = (tmp_path / "performance_report.md").read_text()
    assert "5D" in report and "10 bp" in report
    assert "1D" not in report and "20 bp" not in report
    assert "独立 OOS" in report
