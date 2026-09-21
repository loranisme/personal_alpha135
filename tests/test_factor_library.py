import pytest

from us_equity_alpha.factor_library import build_registry_view, compute_sec_registry_factors


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


def _sec_case(tmp_path):
    from test_sec_fundamentals import _write_release
    from us_equity_alpha.sec_field_mapping import SecFieldBinding
    from us_equity_alpha.sec_factor_inputs import load_sec_factor_inputs
    from us_equity_alpha.sec_fundamentals import SecFundamentalProvider

    provider = SecFundamentalProvider(_write_release(tmp_path / "release"))
    binding = SecFieldBinding("sales", "revenue", "TTM", "SOURCE_LINKED_DIRECT", "AS_REPORTED", "SEC_SOURCE_LINKED", "reviewed")
    inputs, evidence = load_sec_factor_inputs(provider, [binding], "2025-01-02", "2025-01-03")
    row = {
        "local_factor_id": "sec_factor_a",
        "expression": "sec__revenue__TTM * 2",
        "settings": {"delay": 0, "decay": 0, "neutralization": "NONE"},
        "field_bindings": {"sec__revenue__TTM": "revenue/TTM"},
        "build_status": "COMPILED",
        "usage_tier": "DIAGNOSTIC_ONLY",
        "provenance_kind": "SEC_SOURCE_LINKED",
        "sec_evidence_hashes": {
            key: evidence[key] for key in (
                "dataset_version", "processed_manifest_sha256", "canonical_mapping_hash",
                "derived_field_catalog_hash", "security_mapping_hash", "pit_contract_hash",
            )
        },
    }
    return {"factors": [row]}, inputs, evidence


def test_sec_compute_requires_hash_bound_evidence(tmp_path):
    library, inputs, _ = _sec_case(tmp_path)
    with pytest.raises(ValueError, match="SEC_INPUT_EVIDENCE_MISSING"):
        compute_sec_registry_factors(library, ["sec_factor_a"], inputs, {})


def test_sec_compute_rejects_unrequested_field(tmp_path):
    library, inputs, evidence = _sec_case(tmp_path)
    inputs["sec__future_field__TTM"] = inputs["sec__revenue__TTM"]
    with pytest.raises(ValueError, match="SEC_INPUT_SET_MISMATCH"):
        compute_sec_registry_factors(library, ["sec_factor_a"], inputs, evidence)


def test_sec_compute_rejects_mutated_panel(tmp_path):
    library, inputs, evidence = _sec_case(tmp_path)
    inputs["sec__revenue__TTM"].iloc[0, 0] = 999.0
    with pytest.raises(ValueError, match="SEC_INPUT_PANEL_HASH_MISMATCH"):
        compute_sec_registry_factors(library, ["sec_factor_a"], inputs, evidence)


def test_sec_compute_rejects_factor_from_another_release(tmp_path):
    library, inputs, evidence = _sec_case(tmp_path)
    library["factors"][0]["sec_evidence_hashes"]["processed_manifest_sha256"] = "f" * 64
    with pytest.raises(ValueError, match="SEC_FACTOR_EVIDENCE_MISMATCH"):
        compute_sec_registry_factors(library, ["sec_factor_a"], inputs, evidence)


def test_sec_compute_rejects_inconsistent_release_hashes(tmp_path):
    library, inputs, evidence = _sec_case(tmp_path)
    evidence["output:canonical_field_catalog.csv"] = "f" * 64
    with pytest.raises(ValueError, match="SEC_INPUT_EVIDENCE_INCONSISTENT"):
        compute_sec_registry_factors(library, ["sec_factor_a"], inputs, evidence)


def test_sec_compute_rejects_declared_basis_mismatch(tmp_path):
    library, inputs, evidence = _sec_case(tmp_path)
    library["factors"][0]["field_bindings"]["sec__revenue__TTM"] = "revenue/INSTANT"
    with pytest.raises(ValueError, match="SEC_FACTOR_FIELD_BASIS_MISMATCH"):
        compute_sec_registry_factors(library, ["sec_factor_a"], inputs, evidence)


def test_sec_proxy_uses_same_diagnostic_adapter(tmp_path):
    library, inputs, evidence = _sec_case(tmp_path)
    library["factors"][0]["provenance_kind"] = "SEC_HYPOTHESIS_PROXY"
    result = compute_sec_registry_factors(library, ["sec_factor_a"], inputs, evidence)
    assert result["sec_factor_a"].loc["2025-01-02", "sec-a"] == 20.0


def test_sec_strict_factor_rejects_proxy_field_binding(tmp_path):
    library, inputs, evidence = _sec_case(tmp_path)
    evidence["binding_tracks_by_alias"] = {"sec__revenue__TTM": "SEC_HYPOTHESIS_PROXY"}
    with pytest.raises(ValueError, match="SEC_STRICT_PROXY_INPUT"):
        compute_sec_registry_factors(library, ["sec_factor_a"], inputs, evidence)


def test_sec_compute_rejects_research_and_wrong_provenance(tmp_path):
    library, inputs, evidence = _sec_case(tmp_path)
    with pytest.raises(ValueError, match="SEC_DIAGNOSTIC_ONLY"):
        compute_sec_registry_factors(library, ["sec_factor_a"], inputs, evidence, usage="research")
    library["factors"][0]["provenance_kind"] = "LOCAL_RECONSTRUCTION"
    with pytest.raises(ValueError, match="SEC_PROVENANCE_INVALID"):
        compute_sec_registry_factors(library, ["sec_factor_a"], inputs, evidence)


def test_sec_compute_preserves_factor_engine_result(tmp_path):
    import pandas as pd

    library, inputs, evidence = _sec_case(tmp_path)
    result = compute_sec_registry_factors(library, ["sec_factor_a"], inputs, evidence)
    pd.testing.assert_frame_equal(result["sec_factor_a"], inputs["sec__revenue__TTM"] * 2)
