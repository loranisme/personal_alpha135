import pandas as pd
import pytest

from test_sec_fundamentals import _base_identity, _base_panel, _write_release
from us_equity_alpha.sec_field_mapping import SecFieldBinding
from us_equity_alpha.sec_factor_inputs import binding_alias, load_sec_factor_inputs
from us_equity_alpha.sec_fundamentals import SecFundamentalProvider


def _binding(field="revenue", basis="TTM", brain="sales"):
    return SecFieldBinding(
        brain, field, basis, "SOURCE_LINKED_DIRECT", "AS_REPORTED",
        "SEC_SOURCE_LINKED", "reviewed",
    )


def test_alias_encodes_exact_field_and_basis():
    assert binding_alias(_binding()) == "sec__revenue__TTM"
    assert binding_alias(_binding("revenue", "INSTANT")) == "sec__revenue__INSTANT"


def test_loader_preserves_sessions_and_binds_release_evidence(tmp_path):
    provider = SecFundamentalProvider(_write_release(tmp_path / "release"))
    panels, evidence = load_sec_factor_inputs(
        provider, [_binding()], "2025-01-02", "2025-01-03"
    )
    assert set(panels) == {"sec__revenue__TTM"}
    panel = panels["sec__revenue__TTM"]
    assert list(panel.index) == [pd.Timestamp("2025-01-02"), pd.Timestamp("2025-01-03")]
    assert panel.index.freq is None
    assert list(panel.columns) == ["sec-a", "sec-entity"]
    assert panel.loc["2025-01-02", "sec-a"] == 10.0
    assert evidence["dataset_version"] == "SEC_PIT_FUNDAMENTALS_V1"
    assert evidence["processed_manifest_sha256"] == provider.evidence_hashes["processed_manifest_sha256"]
    assert evidence["canonical_mapping_hash"] == provider.evidence_hashes["canonical_mapping_hash"]
    assert evidence["availability_mode"] == "HISTORICAL_DIAGNOSTIC"
    assert evidence["start"] == "2025-01-02"
    assert evidence["end"] == "2025-01-03"
    assert evidence["security_ids"] == ["sec-a", "sec-entity"]
    assert evidence["field_basis_by_alias"] == {"sec__revenue__TTM": ["revenue", "TTM"]}
    assert evidence["binding_tracks_by_alias"] == {"sec__revenue__TTM": "SEC_SOURCE_LINKED"}
    assert len(evidence["panel_sha256"]["sec__revenue__TTM"]) == 64


def test_loader_rejects_unsupported_basis_without_substitution(tmp_path):
    provider = SecFundamentalProvider(_write_release(tmp_path / "release"))
    with pytest.raises(ValueError, match="SEC_FIELD_BASIS_UNKNOWN:revenue:INSTANT"):
        load_sec_factor_inputs(provider, [_binding("revenue", "INSTANT")], "2025-01-02", "2025-01-03")


def test_loader_rejects_duplicate_alias_with_conflicting_binding(tmp_path):
    provider = SecFundamentalProvider(_write_release(tmp_path / "release"))
    with pytest.raises(ValueError, match="SEC_INPUT_ALIAS_DUPLICATE"):
        load_sec_factor_inputs(provider, [_binding(), _binding(brain="sales2")], "2025-01-02", "2025-01-03")


def test_loader_aligns_sparse_security_columns_with_missing_values(tmp_path):
    identity = _base_identity()
    identity.loc[identity.security_id.eq("sec-entity"), "mapping_status"] = "VERIFIED_SINGLE_CLASS"
    provider = SecFundamentalProvider(_write_release(tmp_path / "release", identity=identity))
    shares = SecFieldBinding(
        "sharesout", "shares_outstanding_sec", "INSTANT", "HYPOTHESIS_PROXY",
        "AS_REPORTED", "SEC_HYPOTHESIS_PROXY", "reviewed",
    )
    panels, evidence = load_sec_factor_inputs(
        provider, [_binding(), shares], "2025-01-02", "2025-01-03"
    )
    assert list(panels["sec__shares_outstanding_sec__INSTANT"].columns) == ["sec-a", "sec-entity"]
    assert pd.isna(panels["sec__shares_outstanding_sec__INSTANT"].loc["2025-01-02", "sec-a"])
    assert evidence["security_ids"] == ["sec-a", "sec-entity"]


def test_strict_sales_omits_bank_proxy_but_proxy_sales_can_use_it(tmp_path):
    panel = _base_panel()
    panel.loc[panel.security_id.eq("sec-entity") & panel.canonical_field.eq("revenue"),
              "semantic_grade"] = "HYPOTHESIS_PROXY"
    provider = SecFundamentalProvider(_write_release(tmp_path / "bank", panel=panel))
    strict, strict_evidence = load_sec_factor_inputs(
        provider, [_binding()], "2025-01-02", "2025-01-03"
    )
    assert "sec-entity" not in strict["sec__revenue__TTM"].columns
    assert strict_evidence["binding_tracks_by_alias"] == {"sec__revenue__TTM": "SEC_SOURCE_LINKED"}

    proxy_binding = SecFieldBinding(
        "sales", "revenue", "TTM", "HYPOTHESIS_PROXY", "AS_REPORTED",
        "SEC_HYPOTHESIS_PROXY", "Reviewed bank net revenue proxy",
    )
    proxy, evidence = load_sec_factor_inputs(
        provider, [proxy_binding], "2025-01-02", "2025-01-03"
    )
    assert proxy["sec__revenue__TTM"]["sec-entity"].tolist() == [20.0, 20.0]
    assert evidence["binding_tracks_by_alias"] == {"sec__revenue__TTM": "SEC_HYPOTHESIS_PROXY"}
