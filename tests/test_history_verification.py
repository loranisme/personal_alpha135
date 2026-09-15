import hashlib
import json
from pathlib import Path

import pandas as pd
import pytest

from us_equity_alpha.history_verification import (
    download_tiingo_benchmark,
    load_tiingo_history_snapshot,
)
from us_equity_alpha.reconstruction_pipeline import (
    _implementation_files,
    _replay_input_path,
    run_reconstruction_pipeline,
)


def write_raw(root, symbol, rows):
    path = root / "raw" / f"{symbol}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(rows))
    path.with_suffix(".sha256").write_text(hashlib.sha256(path.read_bytes()).hexdigest())
    return path


def rows(symbol_offset=0):
    return [
        {
            "date": f"2024-01-{day:02d}T00:00:00.000Z",
            "open": 100 + symbol_offset + day,
            "high": 102 + symbol_offset + day,
            "low": 99 + symbol_offset + day,
            "close": 101 + symbol_offset + day,
            "volume": 1000 + day,
        }
        for day in range(1, 6)
    ]


def test_loads_hashed_raw_history_and_aligns_optional_spy(tmp_path):
    root = tmp_path / "history"
    root.mkdir()
    pd.DataFrame({"Symbol": ["A", "B"]}).to_csv(root / "universe.csv", index=False)
    write_raw(root, "A", rows())
    write_raw(root, "B", rows(10))
    spy = write_raw(tmp_path / "benchmark", "SPY", rows(20))
    fields, evidence = load_tiingo_history_snapshot(root, 2, spy)
    assert list(fields["close"].columns) == ["A", "B", "SPY"]
    assert fields["close"].index.tz is not None
    assert evidence["symbols"] == 3
    assert evidence["historical_pit_verified"] is False
    assert evidence["source_kind"] == "DEVELOPMENT_DIAGNOSTIC"
    assert evidence["benchmark_sha256"] == hashlib.sha256(spy.read_bytes()).hexdigest()


def test_rejects_raw_history_hash_mismatch(tmp_path):
    root = tmp_path / "history"
    root.mkdir()
    pd.DataFrame({"Symbol": ["A"]}).to_csv(root / "universe.csv", index=False)
    path = write_raw(root, "A", rows())
    path.write_text("[]")
    with pytest.raises(ValueError, match="RAW_HASH_MISMATCH:A"):
        load_tiingo_history_snapshot(root, 1)


def test_v3_evidence_labels_include_new_implementation_and_history_path():
    assert {"derived_capabilities.py", "history_verification.py"} <= set(
        _implementation_files()
    )
    assert _replay_input_path("tiingo_eod", {"provider": "tiingo_eod"}) == (
        "HASHED_LONG_HISTORY_BARS"
    )
    assert _replay_input_path("alpaca_sip", {"provider": "tiingo_eod"}) == (
        "ORDINARY_PROVIDER_BARS"
    )


def test_download_tiingo_benchmark_writes_hashed_secret_free_evidence(tmp_path):
    requested = {}

    class Response:
        status_code = 200

        @staticmethod
        def json():
            return rows()

    class Session:
        def get(self, url, **kwargs):
            requested.update(url=url, **kwargs)
            return Response()

    output = tmp_path / "SPY.json"
    evidence = download_tiingo_benchmark(
        output,
        token="do-not-persist",
        start_date="2019-10-01",
        end_date="2025-12-31",
        session=Session(),
    )
    assert requested["headers"] == {"Authorization": "Token do-not-persist"}
    assert requested["params"] == {
        "startDate": "2019-10-01",
        "endDate": "2025-12-31",
    }
    assert output.with_suffix(".sha256").read_text().strip() == hashlib.sha256(
        output.read_bytes()
    ).hexdigest()
    assert evidence["symbol"] == "SPY"
    assert evidence["rows"] == 5
    persisted = output.read_text() + json.dumps(evidence)
    assert "do-not-persist" not in persisted


def test_private_long_history_promotes_nonbenchmark_compiled_factors(tmp_path):
    root = Path(__file__).resolve().parents[1]
    registry = root / "private/runs/t1-live-import-20260907-v5/alpha_registry.json"
    history = root / "private/runs/expanded-500-2020-2025-v1"
    if not registry.exists() or not history.exists():
        pytest.skip("private evidence unavailable")
    mapping = root / "private/runs/t2-live-mapping-20260907-v2/field_mapping.json"
    providers = {
        "alpaca_sip": root / "private/runs/one-alpha-alpaca-authorized-v1",
        "tiingo_eod": root / "private/runs/one-alpha-tiingo-authorized-v1",
    }
    result = run_reconstruction_pipeline(
        registry,
        mapping,
        providers,
        tmp_path / "library",
        history_snapshot=history,
        history_limit=50,
    )
    assert result["long_history_verification"]["symbols"] == 50
    assert result["compute_verified_count"] >= 91
    assert result["compute_verified_source_count"] >= 198
    assert result["research_eligible_count"] == result["released_count"] == 0
