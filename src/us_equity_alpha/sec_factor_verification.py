"""Engineering verification for hash-bound, diagnostic SEC factor candidates.

This module measures calculability and PIT timing only. It does not read returns,
rank candidates by performance, or promote a factor into a trading pool.
"""

from __future__ import annotations

import copy
from collections.abc import Mapping, Sequence
from typing import Any

import numpy as np
import pandas as pd

from .factor_library import compute_sec_registry_factors
from .factors import evaluate_factor
from .sec_factor_inputs import verify_sec_input_evidence


_RELEASE_KEYS = (
    "dataset_version", "processed_manifest_sha256", "canonical_mapping_hash",
    "derived_field_catalog_hash", "security_mapping_hash", "pit_contract_hash",
    "universe_snapshot_hash", "raw_manifest_hash",
)


def _candidate_inputs(
    candidate: Mapping[str, Any], inputs: Mapping[str, pd.DataFrame],
    evidence: Mapping[str, Any],
) -> tuple[dict[str, pd.DataFrame], dict[str, Any]]:
    """Verify the whole input bundle, then retain this factor's exact aliases."""
    verify_sec_input_evidence(inputs, evidence)
    declared = candidate.get("field_bindings")
    required = candidate.get("required_fields", list(declared) if isinstance(declared, Mapping) else None)
    if (not isinstance(declared, Mapping) or not isinstance(required, (list, tuple))
            or not required or len(required) != len(set(required)) or set(required) != set(declared)
            or not set(required).issubset(inputs)):
        raise ValueError("SEC_FACTOR_REQUIRED_FIELDS_INVALID")
    expected = candidate.get("sec_evidence_hashes")
    if not isinstance(expected, Mapping) or any(
        expected.get(key) != evidence.get(key) for key in _RELEASE_KEYS
    ):
        raise ValueError("SEC_FACTOR_EVIDENCE_MISMATCH")
    # The evaluator is intentionally not trusted with the caller's panels.
    selected = {name: inputs[name].copy(deep=True) for name in sorted(required)}
    scoped_evidence = copy.deepcopy(dict(evidence))
    for key in ("field_basis_by_alias", "binding_tracks_by_alias", "panel_sha256"):
        scoped_evidence[key] = {name: evidence[key][name] for name in selected}
    return selected, scoped_evidence


def _prefix_lengths(session_count: int) -> list[int]:
    if session_count <= 1:
        return []
    if session_count <= 5:
        return list(range(1, session_count))
    return sorted({max(1, int(session_count * fraction)) for fraction in (0.25, 0.5, 0.75)})


def _same(left: pd.DataFrame, right: pd.DataFrame) -> bool:
    try:
        pd.testing.assert_frame_equal(left, right, check_exact=True)
    except AssertionError:
        return False
    return True


def _finite_mask(frame: pd.DataFrame) -> np.ndarray:
    try:
        values = frame.to_numpy(dtype=float, na_value=np.nan)
    except (TypeError, ValueError) as exc:
        raise ValueError("NON_NUMERIC_FACTOR_OUTPUT") from exc
    return np.isfinite(values)


def _coverage(record: dict[str, Any], values: pd.DataFrame,
              inputs: Mapping[str, pd.DataFrame]) -> None:
    finite = _finite_mask(values)
    counts = finite.sum(axis=1)
    valid_sessions = counts >= 2
    record.update({
        "requested_session_count": len(values.index),
        "sessions_with_two_finite_values": int(valid_sessions.sum()),
        "first_valid_session": (
            str(values.index[np.flatnonzero(valid_sessions)[0]].date())
            if valid_sessions.any() else None
        ),
        "last_valid_session": (
            str(values.index[np.flatnonzero(valid_sessions)[-1]].date())
            if valid_sessions.any() else None
        ),
        "security_count": len(values.columns),
        "securities_ever_valid": int(finite.any(axis=0).sum()),
        "daily_finite_count_min": int(counts.min()),
        "daily_finite_count_median": float(np.median(counts)),
        "daily_finite_count_max": int(counts.max()),
        "factor_missing_fraction": float(1 - finite.mean()),
        "field_missing_fractions": {
            name: float(1 - _finite_mask(frame).mean())
            for name, frame in sorted(inputs.items())
        },
        "coverage_status": "MEASURED" if valid_sessions.all() else "LIMITED",
    })


def _mutated_after(inputs: Mapping[str, pd.DataFrame], length: int
                   ) -> dict[str, pd.DataFrame]:
    changed: dict[str, pd.DataFrame] = {}
    for ordinal, (alias, frame) in enumerate(sorted(inputs.items())):
        copy_frame = frame.copy(deep=True)
        for position in range(length, len(copy_frame)):
            copy_frame.iloc[position, :] = (
                1_000_000.0 + ordinal * 100_000.0 + position * 1_000.0
                + np.arange(len(copy_frame.columns), dtype=float)
            )
        changed[alias] = copy_frame
    return changed


def verify_sec_factor(
    candidate: Mapping[str, Any], inputs: Mapping[str, pd.DataFrame],
    input_evidence: Mapping[str, Any],
) -> tuple[dict[str, Any], pd.DataFrame | None]:
    """Replay one candidate and reject future-dependent or unusable matrices.

    Partial field coverage is descriptive; only a wholly empty cross-sectional
    sample fails the coverage gate. All checks remain diagnostic only.
    """
    if not isinstance(candidate, Mapping):
        raise TypeError("SEC_FACTOR_CANDIDATE_INVALID")
    record = copy.deepcopy(dict(candidate))
    record.update({
        "build_status": "COMPUTE_FAILED", "verification_failures": [],
        "deterministic_replay": "NOT_RUN", "prefix_invariance": "NOT_RUN",
        "no_future_mutation": "NOT_RUN", "coverage_status": "NOT_MEASURED",
    })
    failures: list[str] = record["verification_failures"]
    try:
        if candidate.get("build_status") != "COMPILED":
            raise ValueError("SEC_FACTOR_NOT_COMPILED")
        selected, evidence = _candidate_inputs(candidate, inputs, input_evidence)
        # Reuse the registry's source/proxy, binding, usage and release gates.
        compute_sec_registry_factors(
            {"factors": [dict(candidate)]}, [candidate["local_factor_id"]],
            selected, evidence,
        )
        expression = candidate["expression"]
        settings = candidate["settings"]
        first = evaluate_factor(expression, selected, settings)
        second = evaluate_factor(expression, selected, settings)
        if not isinstance(first, pd.DataFrame) or not isinstance(second, pd.DataFrame):
            raise ValueError("FACTOR_RESULT_NOT_MATRIX")
        if not first.index.equals(next(iter(selected.values())).index) or not first.columns.equals(
            next(iter(selected.values())).columns
        ):
            raise ValueError("FACTOR_OUTPUT_ALIGNMENT_MISMATCH")
        if not _same(first, second):
            failures.append("DETERMINISTIC_REPLAY_FAILED")
        else:
            record["deterministic_replay"] = "PASS"
        try:
            numeric = first.to_numpy(dtype=float, na_value=np.nan)
        except (TypeError, ValueError) as exc:
            raise ValueError("NON_NUMERIC_FACTOR_OUTPUT") from exc
        if np.isinf(numeric).any():
            failures.append("INFINITE_FACTOR_OUTPUT")
        _coverage(record, first, selected)
        if not record["sessions_with_two_finite_values"]:
            failures.append("NO_VALID_CROSS_SECTIONAL_OBSERVATIONS")
        lengths = _prefix_lengths(len(first))
        record["prefix_check_count"] = len(lengths)
        if len(lengths) < 2:
            failures.append("INSUFFICIENT_CHRONOLOGICAL_PREFIXES")
        else:
            prefix_ok = True
            mutation_ok = True
            for length in lengths:
                prefix_inputs = {name: frame.iloc[:length].copy() for name, frame in selected.items()}
                prefix_value = evaluate_factor(expression, prefix_inputs, settings)
                if not isinstance(prefix_value, pd.DataFrame) or not _same(
                    prefix_value, first.iloc[:length]
                ):
                    prefix_ok = False
                mutated = _mutated_after(selected, length)
                changed_value = evaluate_factor(expression, mutated, settings)
                if not isinstance(changed_value, pd.DataFrame) or not _same(
                    changed_value.iloc[:length], first.iloc[:length]
                ):
                    mutation_ok = False
            if prefix_ok:
                record["prefix_invariance"] = "PASS"
            else:
                failures.append("PREFIX_INVARIANCE_FAILED")
            if mutation_ok:
                record["no_future_mutation"] = "PASS"
            else:
                failures.append("NO_FUTURE_MUTATION_FAILED")
        # Catch accidental in-place mutation even when the resulting factor is
        # numerically stable across replay and prefixes.
        verify_sec_input_evidence(selected, evidence)
    except (KeyError, TypeError, ValueError, OverflowError) as exc:
        failures.append(str(exc) or type(exc).__name__)
    except Exception as exc:
        failures.append("SEC_FACTOR_EVALUATION_EXCEPTION:" + type(exc).__name__)
    if failures:
        record["verification_failures"] = sorted(set(failures))
        return record, None
    record["build_status"] = "COMPUTE_VERIFIED"
    return record, first


def verify_sec_candidates(
    candidates: Sequence[Mapping[str, Any]], inputs: Mapping[str, pd.DataFrame],
    input_evidence: Mapping[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, pd.DataFrame], pd.DataFrame]:
    """Return candidate decisions, passed matrices and a descriptive coverage table."""
    ids = [candidate.get("local_factor_id") for candidate in candidates]
    if any(not isinstance(item, str) or not item for item in ids) or len(ids) != len(set(ids)):
        raise ValueError("SEC_FACTOR_ID_DUPLICATE_OR_MISSING")
    records: list[dict[str, Any]] = []
    matrices: dict[str, pd.DataFrame] = {}
    coverage_rows: list[dict[str, Any]] = []
    coverage_keys = (
        "coverage_status", "requested_session_count", "sessions_with_two_finite_values",
        "first_valid_session", "last_valid_session", "security_count", "securities_ever_valid",
        "daily_finite_count_min", "daily_finite_count_median", "daily_finite_count_max",
        "factor_missing_fraction", "field_missing_fractions",
    )
    for candidate in candidates:
        record, values = verify_sec_factor(candidate, inputs, input_evidence)
        records.append(record)
        if values is not None:
            matrices[record["local_factor_id"]] = values
        coverage_rows.append({"local_factor_id": record["local_factor_id"],
                              "build_status": record["build_status"],
                              **{key: record.get(key) for key in coverage_keys}})
    return records, matrices, pd.DataFrame(coverage_rows)
