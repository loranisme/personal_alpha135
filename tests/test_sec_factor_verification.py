"""Engineering-only checks for SEC PIT factor candidates."""

from __future__ import annotations

import copy

import numpy as np
import pandas as pd
import pytest

from us_equity_alpha.sec_factor_inputs import _panel_sha256
from us_equity_alpha.sec_factor_verification import verify_sec_candidates, verify_sec_factor


@pytest.fixture
def daily_case():
    dates = pd.bdate_range("2024-12-24", periods=10)
    columns = ["sec-a", "sec-b", "sec-c"]
    revenue = pd.DataFrame(
        np.arange(1, 31, dtype=float).reshape(10, 3), index=dates, columns=columns
    )
    assets = revenue * 2
    inputs = {"sec__revenue__TTM": revenue, "sec__assets__INSTANT": assets}
    hashes = {key: "a" * 64 for key in (
        "processed_manifest_sha256", "canonical_mapping_hash", "derived_field_catalog_hash",
        "security_mapping_hash", "pit_contract_hash", "universe_snapshot_hash",
        "raw_manifest_hash",
    )}
    evidence = {
        "dataset_version": "SEC_PIT_FUNDAMENTALS_V1", **hashes,
        "output:canonical_field_catalog.csv": hashes["canonical_mapping_hash"],
        "output:derived_field_definitions.json": hashes["derived_field_catalog_hash"],
        "output:cik_security_map.csv": hashes["security_mapping_hash"],
        "output:daily_pit_fundamental_panel.parquet": "b" * 64,
        "availability_mode": "HISTORICAL_DIAGNOSTIC",
        "start": str(dates[0].date()), "end": str(dates[-1].date()),
        "security_ids": columns,
        "field_basis_by_alias": {
            "sec__revenue__TTM": ["revenue", "TTM"],
            "sec__assets__INSTANT": ["assets", "INSTANT"],
        },
        "binding_tracks_by_alias": {
            "sec__revenue__TTM": "SEC_SOURCE_LINKED",
            "sec__assets__INSTANT": "SEC_SOURCE_LINKED",
        },
        "panel_sha256": {name: _panel_sha256(panel) for name, panel in inputs.items()},
    }
    candidate = {
        "local_factor_id": "sec-sl-test", "expression": "rank(sec__revenue__TTM)",
        "settings": {"delay": 0, "decay": 0, "neutralization": "NONE"},
        "field_bindings": {"sec__revenue__TTM": "revenue/TTM"},
        "required_fields": ["sec__revenue__TTM"],
        "build_status": "COMPILED", "coverage_status": "NOT_MEASURED",
        "usage_tier": "DIAGNOSTIC_ONLY", "provenance_kind": "SEC_SOURCE_LINKED",
        "sec_evidence_hashes": {key: evidence[key] for key in ("dataset_version", *hashes)},
    }
    return candidate, inputs, evidence


def test_compute_verified_requires_deterministic_replay_and_measures_coverage(daily_case):
    candidate, inputs, evidence = daily_case
    record, values = verify_sec_factor(candidate, inputs, evidence)
    assert record["build_status"] == "COMPUTE_VERIFIED"
    assert record["deterministic_replay"] == "PASS"
    assert record["prefix_invariance"] == "PASS"
    assert record["no_future_mutation"] == "PASS"
    assert record["coverage_status"] == "MEASURED"
    assert record["requested_session_count"] == 10
    assert record["sessions_with_two_finite_values"] == 10
    assert record["security_count"] == 3
    assert record["field_missing_fractions"] == {"sec__revenue__TTM": 0.0}
    assert set(values.columns) == {"sec-a", "sec-b", "sec-c"}
    assert not any("return" in name.lower() or "performance" in name.lower() for name in record)


def test_hash_mismatch_fails_before_computation(daily_case):
    candidate, inputs, evidence = daily_case
    changed = {name: panel.copy() for name, panel in inputs.items()}
    changed["sec__revenue__TTM"].iloc[0, 0] = 999
    record, values = verify_sec_factor(candidate, changed, evidence)
    assert values is None
    assert record["build_status"] == "COMPUTE_FAILED"
    assert any("PANEL_HASH_MISMATCH" in reason for reason in record["verification_failures"])


def test_candidate_cannot_use_a_different_sec_release(daily_case):
    candidate, inputs, evidence = daily_case
    candidate = copy.deepcopy(candidate)
    candidate["sec_evidence_hashes"]["raw_manifest_hash"] = "f" * 64
    record, values = verify_sec_factor(candidate, inputs, evidence)
    assert values is None
    assert "SEC_FACTOR_EVIDENCE_MISMATCH" in record["verification_failures"]


def test_prefix_invariance_detects_future_dependency(daily_case, monkeypatch):
    candidate, inputs, evidence = daily_case

    def future_leaking_evaluator(expression, fields, settings):
        panel = fields["sec__revenue__TTM"]
        return panel + panel.iloc[-1]

    monkeypatch.setattr("us_equity_alpha.sec_factor_verification.evaluate_factor", future_leaking_evaluator)
    record, values = verify_sec_factor(candidate, inputs, evidence)
    assert values is None
    assert "PREFIX_INVARIANCE_FAILED" in record["verification_failures"]


def test_future_fact_mutation_cannot_change_prior_scores(daily_case):
    candidate, inputs, evidence = daily_case
    record, values = verify_sec_factor(candidate, inputs, evidence)
    assert record["no_future_mutation"] == "PASS"
    changed = {name: panel.copy() for name, panel in inputs.items()}
    cutoff = inputs["sec__revenue__TTM"].index[5]
    changed["sec__revenue__TTM"].loc[cutoff:] *= 1000
    changed_evidence = copy.deepcopy(evidence)
    changed_evidence["panel_sha256"]["sec__revenue__TTM"] = _panel_sha256(
        changed["sec__revenue__TTM"]
    )
    changed_record, changed_values = verify_sec_factor(candidate, changed, changed_evidence)
    assert changed_record["build_status"] == "COMPUTE_VERIFIED"
    pd.testing.assert_frame_equal(values.loc[:cutoff].iloc[:-1], changed_values.loc[:cutoff].iloc[:-1])


def test_future_mutation_gate_catches_conditional_future_dependency(daily_case, monkeypatch):
    candidate, inputs, evidence = daily_case

    def conditional_future_leak(expression, fields, settings):
        panel = fields["sec__revenue__TTM"]
        return panel + (100 if panel.iloc[-1].max() > 1_000 else 0)

    monkeypatch.setattr("us_equity_alpha.sec_factor_verification.evaluate_factor", conditional_future_leak)
    record, values = verify_sec_factor(candidate, inputs, evidence)
    assert values is None
    assert "NO_FUTURE_MUTATION_FAILED" in record["verification_failures"]
    assert record["prefix_invariance"] == "PASS"


def test_infinite_output_is_rejected(daily_case):
    candidate, inputs, evidence = daily_case
    candidate = copy.deepcopy(candidate)
    candidate["expression"] = "sec__revenue__TTM / 0"
    record, values = verify_sec_factor(candidate, inputs, evidence)
    assert values is None
    assert "INFINITE_FACTOR_OUTPUT" in record["verification_failures"]


def test_evaluator_cannot_mutate_callers_hash_bound_input(daily_case, monkeypatch):
    candidate, inputs, evidence = daily_case
    original = inputs["sec__revenue__TTM"].copy()

    def in_place_mutating_evaluator(expression, fields, settings):
        fields["sec__revenue__TTM"].iloc[0, 0] = 999
        return fields["sec__revenue__TTM"].copy()

    monkeypatch.setattr("us_equity_alpha.sec_factor_verification.evaluate_factor", in_place_mutating_evaluator)
    record, values = verify_sec_factor(candidate, inputs, evidence)
    assert values is None
    assert any("SEC_INPUT_PANEL_HASH_MISMATCH" in item for item in record["verification_failures"])
    pd.testing.assert_frame_equal(inputs["sec__revenue__TTM"], original)


def test_zero_observation_factor_fails_without_coverage_threshold(daily_case):
    candidate, inputs, evidence = daily_case
    missing = {key: frame.copy() for key, frame in inputs.items()}
    missing["sec__revenue__TTM"].iloc[:, :] = np.nan
    evidence = copy.deepcopy(evidence)
    evidence["panel_sha256"]["sec__revenue__TTM"] = _panel_sha256(missing["sec__revenue__TTM"])
    record, values = verify_sec_factor(candidate, missing, evidence)
    assert values is None
    assert record["build_status"] == "COMPUTE_FAILED"
    assert record["coverage_status"] == "LIMITED"
    assert "NO_VALID_CROSS_SECTIONAL_OBSERVATIONS" in record["verification_failures"]


def test_sparse_factor_passes_with_descriptive_limited_coverage(daily_case):
    candidate, inputs, evidence = daily_case
    sparse = {key: frame.copy() for key, frame in inputs.items()}
    sparse["sec__revenue__TTM"].iloc[0, :] = np.nan
    evidence = copy.deepcopy(evidence)
    evidence["panel_sha256"]["sec__revenue__TTM"] = _panel_sha256(sparse["sec__revenue__TTM"])
    record, values = verify_sec_factor(candidate, sparse, evidence)
    assert values is not None
    assert record["build_status"] == "COMPUTE_VERIFIED"
    assert record["coverage_status"] == "LIMITED"
    assert record["sessions_with_two_finite_values"] == 9


def test_batch_report_retains_each_factor_and_rejects_duplicate_ids(daily_case):
    candidate, inputs, evidence = daily_case
    other = copy.deepcopy(candidate)
    other["local_factor_id"] = "sec-sl-test-2"
    other["expression"] = "sec__assets__INSTANT / 2"
    other["field_bindings"] = {"sec__assets__INSTANT": "assets/INSTANT"}
    other["required_fields"] = ["sec__assets__INSTANT"]
    records, matrices, coverage = verify_sec_candidates([candidate, other], inputs, evidence)
    assert len(records) == len(matrices) == len(coverage) == 2
    assert set(matrices) == {"sec-sl-test", "sec-sl-test-2"}
    assert set(coverage.local_factor_id) == set(matrices)
    with pytest.raises(ValueError, match="SEC_FACTOR_ID_DUPLICATE"):
        verify_sec_candidates([candidate, candidate], inputs, evidence)
