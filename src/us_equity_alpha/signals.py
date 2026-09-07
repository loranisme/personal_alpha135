"""Fixed-universe factor combination and deterministic ranking."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

import pandas as pd


def combine_equal_weight(
    factors: Mapping[str, pd.DataFrame],
    expected_factor_ids: Sequence[str],
    directions: Mapping[str, int] | None = None,
) -> pd.DataFrame:
    expected = list(expected_factor_ids)
    missing = [factor_id for factor_id in expected if factor_id not in factors]
    extra = [factor_id for factor_id in factors if factor_id not in expected]
    if missing:
        raise ValueError("MISSING_FACTOR:" + ",".join(missing))
    if extra:
        raise ValueError("UNEXPECTED_FACTOR:" + ",".join(extra))
    if not expected:
        raise ValueError("EMPTY_FACTOR_SET")
    direction_map = directions or {factor_id: 1 for factor_id in expected}
    if set(direction_map) != set(expected) or any(direction_map[key] not in {-1, 1} for key in expected):
        raise ValueError("INVALID_FACTOR_DIRECTIONS")

    first = factors[expected[0]]
    total = first * direction_map[expected[0]]
    valid = first.notna()
    for factor_id in expected[1:]:
        frame = factors[factor_id]
        if not frame.index.equals(first.index) or not frame.columns.equals(first.columns):
            raise ValueError("FACTOR_ALIGNMENT_MISMATCH")
        total = total + frame * direction_map[factor_id]
        valid &= frame.notna()
    return (total / len(expected)).where(valid)


def select_top_n(scores: pd.Series, n: int, cash_buffer: float = 0.0) -> pd.DataFrame:
    if n < 1:
        raise ValueError("TOP_N_MUST_BE_POSITIVE")
    if not 0 <= cash_buffer < 1:
        raise ValueError("INVALID_CASH_BUFFER")
    table = pd.DataFrame({"ticker": scores.index.astype(str), "score": scores.to_numpy()})
    table = table.sort_values(["score", "ticker"], ascending=[False, True], na_position="last", kind="mergesort").reset_index(drop=True)
    table["rank"] = range(1, len(table) + 1)
    eligible = table["score"].notna() & (table["rank"] <= n)
    table["status"] = eligible.map({True: "BUY", False: "OUT"})
    table["target_weight"] = 0.0
    selected = int(eligible.sum())
    if selected:
        table.loc[eligible, "target_weight"] = (1.0 - cash_buffer) / selected
    return table[["rank", "ticker", "score", "status", "target_weight"]]
