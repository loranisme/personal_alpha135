import hashlib
import json
from pathlib import Path

import pandas as pd
import pytest

from us_equity_alpha.alpha_catalog import (
    build_alpha_catalog,
    build_catalog_review_view,
    write_alpha_catalog,
)
from us_equity_alpha.cli import main
from us_equity_alpha.proxy_converter import required_history_sessions


def _factor(
    factor_id: str,
    *,
    fields: tuple[str, ...] = ("close",),
    providers: tuple[str, ...] = ("alpaca_sip", "tiingo_eod"),
    mechanism: str | None = "momentum",
    build_status: str = "COMPUTE_VERIFIED",
) -> dict:
    tags = [] if mechanism is None else [mechanism]
    return {
        "local_factor_id": factor_id,
        "expression": "rank(ts_delta(close, 5))",
        "expression_hash": hashlib.sha256(factor_id.encode()).hexdigest(),
        "family_id": f"family__{factor_id}",
        "economic_description": {"mechanism_tags": tags},
        "semantic_status": "FULL_INTENT",
        "field_bindings": {field: field for field in fields},
        "allowed_providers": list(providers),
        "direction_basis": "Signed source component; unvalidated locally.",
        "build_status": build_status,
        "source_alpha_ids": [f"brain-{factor_id}"],
        "settings": {"delay": 1, "decay": 0, "neutralization": "NONE"},
    }


def _policy() -> dict:
    return {
        "schema_version": 1,
        "library_version": "reconstruction-v4",
        "default_provider": "tiingo_eod",
        "diagnostic_value_columns": [
            "diagnostic_rank_ic_mean_5d",
            "diagnostic_rank_icir_5d",
            "diagnostic_top_bottom_net_spread_5d",
            "diagnostic_turnover_mean",
        ],
    }


def _diagnostics() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "local_factor_id": "classified",
                "diagnostic_rank_ic_mean_5d": 0.012,
                "diagnostic_rank_icir_5d": 0.31,
                "diagnostic_top_bottom_net_spread_5d": 0.004,
                "diagnostic_turnover_mean": 0.27,
                "diagnostic_status": "DIAGNOSTIC_ONLY",
                "diagnostic_sample_start": "2020-01-02",
                "diagnostic_sample_end": "2025-12-31",
                "diagnostic_universe_label": "SURVIVORSHIP_BIASED_DIAGNOSTIC",
                "diagnostic_provider": "tiingo_eod",
                "diagnostic_cost_model_hash": "a" * 64,
                "diagnostic_input_hash": "b" * 64,
                "diagnostic_missing_reason": None,
            }
        ]
    )


def _write_inputs(tmp_path: Path) -> tuple[Path, Path, Path, Path]:
    library = tmp_path / "library.json"
    v4_manifest = tmp_path / "v4-manifest.json"
    policy = tmp_path / "policy.json"
    diagnostics = tmp_path / "diagnostics.csv"
    library.write_text(
        json.dumps({"schema_version": 2, "factors": [_factor("classified")]}),
        encoding="utf-8",
    )
    library_digest = hashlib.sha256(library.read_bytes()).hexdigest()
    v4_manifest.write_text(json.dumps({"project_factor_library.json": library_digest}), encoding="utf-8")
    policy.write_text(json.dumps(_policy()), encoding="utf-8")
    _diagnostics().to_csv(diagnostics, index=False)
    return library, v4_manifest, policy, diagnostics


def test_required_history_includes_expression_delay_and_decay():
    assert required_history_sessions(
        "ts_mean(close, 20)", {"delay": 1, "decay": 4}
    ) == 23


def test_catalog_preserves_signed_direction_and_exposes_exclusion_reasons():
    library = {
        "factors": [
            _factor("classified"),
            _factor("vwap", fields=("close", "vwap"), providers=("alpaca_sip",), mechanism="vwap_deviation"),
            _factor("unknown", mechanism=None),
        ]
    }
    frame = build_alpha_catalog(library, _policy(), library_version="reconstruction-v4")
    rows = frame.set_index("local_factor_id")
    assert len(frame) == frame.local_factor_id.nunique() == 3
    assert set(frame.direction) == {1}
    assert rows.loc["classified", "default_provider_eligible"]
    assert rows.loc["vwap", "catalog_exclusion_reasons"] == "ALPACA_VWAP_ONLY"
    assert rows.loc["unknown", "catalog_exclusion_reasons"] == "MECHANISM_UNCLASSIFIED"


def test_catalog_can_bind_alpaca_sip_as_default_provider():
    policy = _policy() | {"default_provider": "alpaca_sip"}
    library = {
        "factors": [
            _factor("dual"),
            _factor("alpaca-vwap", fields=("close", "vwap"), providers=("alpaca_sip",), mechanism="vwap_deviation"),
            _factor("tiingo-only", providers=("tiingo_eod",)),
        ]
    }
    rows = build_alpha_catalog(library, policy, library_version="reconstruction-v4").set_index("local_factor_id")
    assert rows.loc["dual", "default_provider_eligible"]
    assert rows.loc["alpaca-vwap", "default_provider_eligible"]
    assert rows.loc["tiingo-only", "catalog_exclusion_reasons"] == "DEFAULT_PROVIDER_UNAVAILABLE"


def test_diagnostic_view_is_read_only_for_catalog_decisions_and_preserves_missing():
    catalog = build_alpha_catalog(
        {"factors": [_factor("classified"), _factor("other", mechanism="realized_risk")]},
        _policy(),
        library_version="reconstruction-v4",
    )
    view = build_catalog_review_view(catalog, _diagnostics(), _policy())
    assert view.direction.tolist() == [1, 1]
    assert view.default_provider_eligible.tolist() == [True, True]
    assert view.usage_status.tolist() == ["LIBRARY_ONLY", "LIBRARY_ONLY"]
    assert view.loc[view.local_factor_id == "classified", "diagnostic_status"].item() == "DIAGNOSTIC_ONLY"
    missing = view.loc[view.local_factor_id == "other"].iloc[0]
    assert pd.isna(missing.diagnostic_rank_ic_mean_5d)
    assert missing.diagnostic_missing_reason == "NOT_SUPPLIED"


@pytest.mark.parametrize("column", ["sharpe", "annualized_return", "maximum_drawdown"])
def test_diagnostic_view_rejects_unapproved_performance_columns(column):
    catalog = build_alpha_catalog(
        {"factors": [_factor("classified")]}, _policy(), library_version="reconstruction-v4"
    )
    with pytest.raises(ValueError, match="INVALID_DIAGNOSTIC_SCHEMA"):
        build_catalog_review_view(catalog, _diagnostics().assign(**{column: 1.0}), _policy())


def test_writer_creates_verified_structural_and_review_artifacts(tmp_path):
    library, v4_manifest, policy, diagnostics = _write_inputs(tmp_path)
    output = tmp_path / "catalog"
    paths = write_alpha_catalog(library, v4_manifest, policy, output, diagnostics)
    assert set(paths) == {"catalog_csv", "catalog_parquet", "review_csv", "summary", "manifest"}
    assert pd.read_csv(paths["catalog_csv"]).shape[0] == 1
    assert pd.read_parquet(paths["catalog_parquet"]).shape[0] == 1
    assert pd.read_csv(paths["review_csv"]).diagnostic_status.tolist() == ["DIAGNOSTIC_ONLY"]
    manifest = json.loads(paths["manifest"].read_text(encoding="utf-8"))
    assert manifest["live_orders_submitted"] == 0
    for name, digest in manifest["files"].items():
        assert hashlib.sha256((output / name).read_bytes()).hexdigest() == digest


def test_cli_builds_catalog_and_refuses_nonempty_output(tmp_path, capsys):
    library, v4_manifest, policy, diagnostics = _write_inputs(tmp_path)
    output = tmp_path / "catalog"
    args = [
        "build-alpha-catalog",
        "--library", str(library),
        "--manifest", str(v4_manifest),
        "--policy", str(policy),
        "--diagnostics", str(diagnostics),
        "--output", str(output),
    ]
    assert main(args) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "PASS"
    assert main(args) == 2
    assert json.loads(capsys.readouterr().out)["status"] == "BLOCKED_CONFIG"
