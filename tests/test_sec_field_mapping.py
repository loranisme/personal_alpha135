import json

import pandas as pd
import pytest

from us_equity_alpha.sec_field_mapping import load_sec_field_bindings


def _row(**changes):
    row = dict(brain_field="assets", canonical_field="total_assets", required_basis="INSTANT",
               semantic_grade="SOURCE_LINKED_DIRECT", direction_or_sign_rule="AS_REPORTED",
               allowed_track="SEC_SOURCE_LINKED", mapping_evidence="Reviewed BRAIN and SEC asset definitions")
    row.update(changes)
    return row


def _catalog():
    return pd.DataFrame([
        dict(canonical_field="total_assets", required_basis="INSTANT", semantic_grade="SOURCE_LINKED_DIRECT", sign_convention="AS_REPORTED"),
        dict(canonical_field="shares_outstanding_sec", required_basis="INSTANT", semantic_grade="HYPOTHESIS_PROXY", sign_convention="AS_REPORTED"),
        dict(canonical_field="market_cap_sec_proxy", required_basis="DAILY_PROXY", semantic_grade="HYPOTHESIS_PROXY", sign_convention="shares and price must be positive"),
        dict(canonical_field="revenue", required_basis="QUARTERLY_DISCRETE_OR_ANNUAL", semantic_grade="SOURCE_LINKED_DIRECT", sign_convention="AS_REPORTED"),
    ])


def _load(tmp_path, rows, catalog=None):
    path = tmp_path / "bindings.json"
    path.write_text(json.dumps(rows))
    return load_sec_field_bindings(path, _catalog() if catalog is None else catalog)


def test_binding_requires_field_basis_and_semantic_grade(tmp_path):
    assert _load(tmp_path, [_row()])["assets"].required_basis == "INSTANT"
    for missing in ("canonical_field", "required_basis", "semantic_grade", "mapping_evidence"):
        row = _row()
        del row[missing]
        with pytest.raises(ValueError):
            _load(tmp_path, [row])


def test_binding_rejects_unknown_basis_duplicate_and_undocumented_sign(tmp_path):
    for rows in ([ _row(canonical_field="unknown") ], [ _row(required_basis="TTM") ],
                 [ _row(), _row() ], [ _row(direction_or_sign_rule="NEGATE") ]):
        with pytest.raises(ValueError):
            _load(tmp_path, rows)


def test_shares_and_cap_are_proxy_only(tmp_path):
    for brain, canonical, basis in (("sharesout", "shares_outstanding_sec", "INSTANT"),
                                    ("cap", "market_cap_sec_proxy", "DAILY_PROXY")):
        row = _row(brain_field=brain, canonical_field=canonical, required_basis=basis,
                   semantic_grade="HYPOTHESIS_PROXY", allowed_track="SEC_HYPOTHESIS_PROXY",
                   direction_or_sign_rule="POSITIVE", mapping_evidence="SEC fact is a proxy for daily BRAIN field")
        assert _load(tmp_path, [row])[brain].allowed_track == "SEC_HYPOTHESIS_PROXY"
        row["allowed_track"] = "SEC_SOURCE_LINKED"
        with pytest.raises(ValueError):
            _load(tmp_path, [row])


def test_ttm_basis_needs_explicit_duration_support(tmp_path):
    row = _row(brain_field="sales", canonical_field="revenue", required_basis="TTM",
               mapping_evidence="Reviewed TTM revenue derivation from eligible discrete quarters")
    assert _load(tmp_path, [row])["sales"].required_basis == "TTM"


def test_issuer_specific_proxy_does_not_disqualify_strict_sales_binding(tmp_path):
    catalog = pd.concat([
        _catalog(),
        pd.DataFrame([dict(canonical_field="revenue",
                           required_basis="QUARTERLY_DISCRETE_OR_ANNUAL",
                           semantic_grade="HYPOTHESIS_PROXY",
                           sign_convention="AS_REPORTED",
                           issuer_cik="0000019617")]),
    ], ignore_index=True)
    row = _row(brain_field="sales", canonical_field="revenue", required_basis="TTM")
    assert _load(tmp_path, [row], catalog)["sales"].allowed_track == "SEC_SOURCE_LINKED"


def test_release_catalog_uses_supported_basis(tmp_path):
    release_catalog = pd.DataFrame([dict(canonical_field="total_assets", supported_basis="INSTANT",
                                        semantic_grade="SOURCE_LINKED_DIRECT")])
    assert _load(tmp_path, [_row()], release_catalog)["assets"].required_basis == "INSTANT"


def test_strict_grade_must_match_the_compatible_basis(tmp_path):
    catalog = pd.DataFrame([
        dict(canonical_field="revenue", required_basis="INSTANT",
             semantic_grade="SOURCE_LINKED_DIRECT", sign_convention="AS_REPORTED"),
        dict(canonical_field="revenue", required_basis="QUARTERLY_DISCRETE_OR_ANNUAL",
             semantic_grade="HYPOTHESIS_PROXY", sign_convention="AS_REPORTED"),
    ])
    row = _row(brain_field="sales", canonical_field="revenue", required_basis="TTM")
    with pytest.raises(ValueError, match="SEC_BINDING_GRADE_MISMATCH"):
        _load(tmp_path, [row], catalog)


def test_strict_direct_binding_cannot_change_reported_sign(tmp_path):
    catalog = pd.DataFrame([dict(canonical_field="total_assets", required_basis="INSTANT",
                                 semantic_grade="SOURCE_LINKED_DIRECT", sign_convention="POSITIVE_OUTFLOW")])
    with pytest.raises(ValueError, match="SEC_BINDING_SIGN_MISMATCH"):
        _load(tmp_path, [_row()], catalog)
