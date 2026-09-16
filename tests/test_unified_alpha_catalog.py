import hashlib
import json

import pandas as pd
import pytest

from us_equity_alpha.alpha_catalog import (
    build_alpha_catalog,
    build_unified_catalog,
    write_unified_catalog,
)
from us_equity_alpha.cli import main
from us_equity_alpha.expansion_registry import register_expansion


def _factor(factor_id: str = "local-a") -> dict:
    return {
        "local_factor_id": factor_id,
        "expression": "rank(ts_delta(close, 5))",
        "expression_hash": hashlib.sha256(factor_id.encode()).hexdigest(),
        "family_id": f"family__{factor_id}",
        "economic_description": {"mechanism_tags": ["runtime-tag-must-not-win"]},
        "semantic_status": "FULL_INTENT",
        "field_bindings": {"close": "close"},
        "allowed_providers": ["alpaca_sip", "tiingo_eod"],
        "direction_basis": "Frozen source sign.",
        "build_status": "COMPUTE_VERIFIED",
        "source_alpha_ids": ["brain-a"],
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


def _v5_catalog(mechanism: str = "catalog-tag") -> pd.DataFrame:
    catalog = build_alpha_catalog(
        {"factors": [_factor()]}, _policy(), library_version="reconstruction-v4"
    )
    catalog.loc[:, "mechanism_tag"] = mechanism
    return catalog


def _expansion(status: str = "COMPUTE_VERIFIED", version: int = 1) -> dict:
    definition = {
        "factor_id": "candidate-a",
        "factor_version": version,
        "expression": "rank(ts_mean(volume, 10))",
        "economic_hypothesis": "Persistent attention is reflected in trading activity.",
        "mechanism_tag": "liquidity_attention",
        "required_fields": ["volume"],
        "allowed_providers": ["tiingo_eod"],
        "settings": {"delay": 1, "decay": 0, "neutralization": "NONE"},
        "direction": 1,
        "parent_factor_ids": [],
        "generation_method": "HUMAN_AUTHORED",
        "created_at": "2026-09-16T00:00:00Z",
        "supersedes_factor_id": None,
        "compute_status": status,
    }
    expression_hash = hashlib.sha256(definition["expression"].encode()).hexdigest()
    lineage = {
        "generation_method": definition["generation_method"],
        "parent_factor_ids": [],
        "supersedes_factor_id": None,
    }
    lineage_hash = hashlib.sha256(
        json.dumps(lineage, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    record = definition | {"expression_hash": expression_hash, "lineage_hash": lineage_hash}
    record["content_hash"] = hashlib.sha256(
        json.dumps(record, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return record


def test_unified_catalog_keeps_every_v4_and_expansion_version_once():
    result = build_unified_catalog(
        {"factors": [_factor()]}, _v5_catalog(), [_expansion()], "tiingo_eod"
    )
    assert len(result) == 2
    assert result[["factor_id", "factor_version"]].duplicated().sum() == 0
    assert set(result.source_kind) == {"V4_BASE", "EXPANSION"}


def test_only_verified_expansions_are_filter_eligible():
    result = build_unified_catalog(
        {"factors": [_factor()]},
        _v5_catalog(),
        [_expansion("REGISTERED"), _expansion("COMPUTE_VERIFIED", version=2)],
        "tiingo_eod",
    )
    rows = result[result.source_kind.eq("EXPANSION")].set_index("factor_version")
    assert not rows.loc[1, "filter_eligible"]
    assert rows.loc[2, "filter_eligible"]
    assert rows.loc[1, "catalog_exclusion_reasons"] == "NOT_COMPUTE_VERIFIED"


def test_v4_mechanism_comes_from_catalog_not_runtime_inference():
    result = build_unified_catalog(
        {"factors": [_factor()]}, _v5_catalog(mechanism="catalog-tag"), [], "tiingo_eod"
    )
    assert result.iloc[0].mechanism_tag == "catalog-tag"


def test_unified_catalog_rejects_incomplete_base_catalog():
    with pytest.raises(ValueError, match="V4_CATALOG_ID_MISMATCH"):
        build_unified_catalog(
            {"factors": [_factor()]}, _v5_catalog().iloc[0:0], [], "tiingo_eod"
        )


def test_writer_and_cli_emit_immutable_verified_bundle(tmp_path, capsys):
    library_path = tmp_path / "library.json"
    base_catalog_path = tmp_path / "base_catalog.csv"
    registry = tmp_path / "registry"
    output = tmp_path / "unified"
    library_path.write_text(json.dumps({"factors": [_factor()]}), encoding="utf-8")
    _v5_catalog().to_csv(base_catalog_path, index=False)
    definition = {key: value for key, value in _expansion().items() if key not in {"expression_hash", "lineage_hash", "content_hash"}}
    register_expansion(definition, registry)
    args = [
        "build-unified-catalog",
        "--library", str(library_path),
        "--base-catalog", str(base_catalog_path),
        "--expansion-registry", str(registry),
        "--provider", "tiingo_eod",
        "--output", str(output),
    ]
    assert main(args) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "PASS"
    assert payload["factor_version_count"] == 2
    assert payload["live_orders_submitted"] == 0
    manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
    for name, digest in manifest["files"].items():
        assert hashlib.sha256((output / name).read_bytes()).hexdigest() == digest
    assert main(args) == 2
    assert json.loads(capsys.readouterr().out)["status"] == "BLOCKED_CONFIG"
