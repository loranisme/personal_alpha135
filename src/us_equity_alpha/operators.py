"""Small, explicitly defined operator subset for the source-supported MVP."""

from __future__ import annotations

from collections.abc import Callable

import numpy as np
import pandas as pd


def _window(window: int) -> int:
    value = int(window)
    if value < 1 or value != window:
        raise ValueError("WINDOW_MUST_BE_POSITIVE_INTEGER")
    return value


def price_delta(values: pd.DataFrame, window: int) -> pd.DataFrame:
    return values - values.shift(_window(window))


def ts_delay(values: pd.DataFrame, window: int) -> pd.DataFrame:
    return values.shift(_window(window))


def ts_mean(values: pd.DataFrame, window: int) -> pd.DataFrame:
    size = _window(window)
    return values.rolling(size, min_periods=size).mean()


def ts_std_dev(values: pd.DataFrame, window: int) -> pd.DataFrame:
    size = _window(window)
    return values.rolling(size, min_periods=size).std(ddof=0)


def ts_rank(values: pd.DataFrame, window: int) -> pd.DataFrame:
    """Percentile rank of the current value in a full trailing window.

    Ties use their average rank. Any missing observation invalidates the window.
    """
    size = _window(window)

    def rank_last(array: np.ndarray) -> float:
        if np.isnan(array).any():
            return np.nan
        return float(pd.Series(array).rank(method="average", pct=True).iloc[-1])

    return values.rolling(size, min_periods=size).apply(rank_last, raw=True)


def cs_rank(values: pd.DataFrame) -> pd.DataFrame:
    return values.rank(axis=1, method="average", pct=True, na_option="keep")


def _group_transform(
    values: pd.DataFrame,
    groups: pd.Series,
    transform: Callable[[pd.DataFrame], pd.DataFrame],
) -> pd.DataFrame:
    aligned = groups.reindex(values.columns)
    output = pd.DataFrame(np.nan, index=values.index, columns=values.columns, dtype=float)
    for group in pd.unique(aligned.dropna()):
        columns = aligned.index[aligned == group]
        output.loc[:, columns] = transform(values.loc[:, columns])
    return output


def group_rank(values: pd.DataFrame, groups: pd.Series) -> pd.DataFrame:
    return _group_transform(values, groups, cs_rank)


def group_neutralize(values: pd.DataFrame, groups: pd.Series) -> pd.DataFrame:
    return _group_transform(values, groups, lambda part: part.sub(part.mean(axis=1), axis=0))


def linear_decay(values: pd.DataFrame, window: int) -> pd.DataFrame:
    size = _window(window)
    weights = np.arange(1, size + 1, dtype=float)
    denominator = float(weights.sum())

    def weighted(array: np.ndarray) -> float:
        if np.isnan(array).any():
            return np.nan
        return float(np.dot(array, weights) / denominator)

    return values.rolling(size, min_periods=size).apply(weighted, raw=True)
