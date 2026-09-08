import pytest

from us_equity_alpha.factor_library import build_registry_view


def factor(local_id="local_proxy__x", usage="DIAGNOSTIC_ONLY"):
    return {
        "local_factor_id": local_id,
        "version": 1,
        "expression": "rank(-returns)",
        "settings": {"delay": 1, "decay": 0, "neutralization": "NONE"},
        "field_bindings": {"returns": "close / ts_delay(close, 1) - 1"},
        "build_status": "COMPILED",
        "usage_tier": usage,
    }


def test_frozen_ids_are_required_and_order_is_preserved():
    library = {"factors": [factor("B"), factor("A")]}
    view = build_registry_view(library, ("A", "B"), usage="diagnostic")
    assert [row["local_factor_id"] for row in view] == ["A", "B"]
    with pytest.raises(ValueError, match="FROZEN_FACTOR_ID_MISSING:C"):
        build_registry_view(library, ("A", "C"), usage="diagnostic")


def test_diagnostic_factor_cannot_enter_research_view():
    with pytest.raises(ValueError, match="FACTOR_NOT_RESEARCH_ELIGIBLE"):
        build_registry_view({"factors": [factor()]}, ("local_proxy__x",), usage="research")


def test_research_view_requires_compute_verification():
    item = factor(usage="RESEARCH_ELIGIBLE")
    with pytest.raises(ValueError, match="FACTOR_NOT_COMPUTE_VERIFIED"):
        build_registry_view({"factors": [item]}, (item["local_factor_id"],), usage="research")


def test_existing_direct_variants_keep_their_factor_values_when_private_evidence_exists():
    from pathlib import Path
    import json
    import pandas as pd

    root = Path(__file__).resolve().parents[1]
    old = root / "private/migrated_library/20260907-v2"
    new = root / "private/project_factor_library/v10"
    if not old.exists() or not new.exists():
        pytest.skip("private integration evidence unavailable")
    library = json.loads((new / "project_factor_library.json").read_text())
    by_source = {source: factor for factor in library["factors"] for source in factor["source_alpha_ids"]}
    for source in ("e7z8gWME", "Wj7wnl3x", "blj2Vvpm", "rKj6pMpm", "1YwVkxvk"):
        factor = by_source[source]
        for old_provider, new_provider in (("alpaca", "alpaca_sip"), ("tiingo", "tiingo_eod")):
            expected = pd.read_parquet(old / "available" / source / f"{old_provider}_factor.parquet")
            observed = pd.read_parquet(new / "factor_values" / factor["local_factor_id"] /
                                       f"{new_provider}.parquet")
            pd.testing.assert_frame_equal(observed, expected)
