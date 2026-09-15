import hashlib
import json

import pandas as pd
import pytest

from us_equity_alpha.current_universe import (
    build_current_liquid_universe,
    write_current_universe_snapshot,
)
from us_equity_alpha.cli import main


AS_OF = pd.Timestamp("2025-12-31 23:00:00", tz="UTC")


def _policy() -> dict:
    return {
        "schema_version": 1,
        "exchanges": ["NYSE", "NASDAQ", "NYSE_AMERICAN"],
        "security_types": ["COMMON_STOCK", "REIT"],
        "minimum_sessions": 252,
        "minimum_raw_close": 5.0,
        "adv_window": 60,
        "minimum_adv_usd": 10_000_000,
        "minimum_universe_size": 500,
        "maximum_universe_size": 1000,
    }


def _inputs(count: int):
    ids = [f"S{index:04d}" for index in range(count)]
    assets = pd.DataFrame(
        {
            "security_id": ids,
            "ticker": [f"T{index:04d}" for index in range(count)],
            "exchange": ["NASDAQ"] * count,
            "status": ["ACTIVE"] * count,
            "tradable": [True] * count,
            "security_type": ["COMMON_STOCK"] * count,
            "domicile": ["US"] * count,
            "source": ["alpaca_assets"] * count,
            "available_at": ["2025-12-31T22:00:00Z"] * count,
        }
    )
    dates = pd.bdate_range(end="2025-12-31", periods=252)
    common = pd.DataFrame(
        {
            "date": dates,
            "close": [10.0] * 252,
            "volume": [1_500_000.0] * 252,
            "adjustment": ["raw"] * 252,
            "source": ["tiingo_eod"] * 252,
            "available_at": ["2025-12-31T22:00:00Z"] * 252,
        }
    )
    bars = {security_id: common for security_id in ids}
    return assets, bars


def test_universe_blocks_499_accepts_500_and_caps_1001_at_1000():
    assets, bars = _inputs(499)
    with pytest.raises(ValueError, match="BLOCKED_INSUFFICIENT_UNIVERSE"):
        build_current_liquid_universe(assets, bars, AS_OF, _policy())

    assets, bars = _inputs(500)
    accepted, evidence = build_current_liquid_universe(assets, bars, AS_OF, _policy())
    assert len(accepted) == evidence["eligible_count"] == 500

    assets, bars = _inputs(1001)
    capped, evidence = build_current_liquid_universe(assets, bars, AS_OF, _policy())
    assert len(capped) == 1000
    assert capped.security_id.tolist() == [f"S{index:04d}" for index in range(1000)]
    assert evidence["pre_cap_eligible_count"] == 1001


def test_current_snapshot_never_claims_historical_pit():
    assets, bars = _inputs(500)
    _, evidence = build_current_liquid_universe(assets, bars, AS_OF, _policy())
    assert evidence["historical_pit_verified"] is False
    assert evidence["historical_reuse_label"] == "SURVIVORSHIP_BIASED_DIAGNOSTIC"
    assert evidence["universe_kind"] == "CURRENT_AS_OF"


def test_asset_and_bar_provenance_fail_closed():
    assets, bars = _inputs(500)
    assets.loc[0, "source"] = None
    with pytest.raises(ValueError, match="ASSET_PROVENANCE_MISSING"):
        build_current_liquid_universe(assets, bars, AS_OF, _policy())

    assets, bars = _inputs(500)
    bad = bars["S0000"].copy()
    bad["adjustment"] = "split_adjusted"
    bars["S0000"] = bad
    with pytest.raises(ValueError, match="BAR_ADJUSTMENT_MUST_BE_RAW"):
        build_current_liquid_universe(assets, bars, AS_OF, _policy())


def test_snapshot_writer_hashes_outputs_and_preserves_current_label(tmp_path):
    assets, bars = _inputs(500)
    output = tmp_path / "universe"
    paths = write_current_universe_snapshot(assets, bars, AS_OF, _policy(), output)
    eligible = pd.read_csv(paths["eligible"])
    evidence = json.loads(paths["evidence"].read_text(encoding="utf-8"))
    manifest = json.loads(paths["manifest"].read_text(encoding="utf-8"))
    assert len(eligible) == 500
    assert evidence["historical_pit_verified"] is False
    for name, digest in manifest["files"].items():
        assert hashlib.sha256((output / name).read_bytes()).hexdigest() == digest


def test_cli_builds_current_universe_from_consolidated_bars(tmp_path, capsys):
    assets, bars = _inputs(500)
    assets_path = tmp_path / "assets.csv"
    bars_dir = tmp_path / "bars"
    policy_path = tmp_path / "policy.json"
    output = tmp_path / "output"
    assets.to_csv(assets_path, index=False)
    bars_dir.mkdir()
    consolidated = pd.concat(
        [frame.assign(security_id=security_id) for security_id, frame in bars.items()],
        ignore_index=True,
    )
    consolidated.to_parquet(bars_dir / "bars.parquet", index=False)
    policy_path.write_text(json.dumps(_policy()), encoding="utf-8")
    code = main([
        "build-current-universe",
        "--assets", str(assets_path),
        "--bars", str(bars_dir),
        "--policy", str(policy_path),
        "--as-of", AS_OF.isoformat(),
        "--output", str(output),
    ])
    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "PASS_CURRENT_ONLY"
    assert len(pd.read_csv(output / "eligible_universe.csv")) == 500
