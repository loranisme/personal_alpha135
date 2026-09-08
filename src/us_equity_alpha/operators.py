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


def ts_sum(values: pd.DataFrame, window: int) -> pd.DataFrame:
    size = _window(window)
    return values.rolling(size, min_periods=size).sum()


def _aligned_pair(left: pd.DataFrame, right: pd.DataFrame) -> None:
    if not left.index.equals(right.index) or not left.columns.equals(right.columns):
        raise ValueError("INPUT_ALIGNMENT_MISMATCH")


def ts_corr(left: pd.DataFrame, right: pd.DataFrame, window: int) -> pd.DataFrame:
    _aligned_pair(left, right)
    size = _window(window)
    return pd.DataFrame({
        column: left[column].rolling(size, min_periods=size).corr(right[column])
        for column in left.columns
    }, index=left.index)


def ts_covariance(left: pd.DataFrame, right: pd.DataFrame, window: int) -> pd.DataFrame:
    _aligned_pair(left, right)
    size = _window(window)
    return pd.DataFrame({
        column: left[column].rolling(size, min_periods=size).cov(right[column], ddof=0)
        for column in left.columns
    }, index=left.index)


def _arg_extreme(values: pd.DataFrame, window: int, *, maximum: bool) -> pd.DataFrame:
    size = _window(window)

    def sessions_back(array: np.ndarray) -> float:
        if np.isnan(array).any():
            return np.nan
        position = int(np.argmax(array) if maximum else np.argmin(array))
        return float(len(array) - 1 - position)

    return values.rolling(size, min_periods=size).apply(sessions_back, raw=True)


def ts_arg_min(values: pd.DataFrame, window: int) -> pd.DataFrame:
    return _arg_extreme(values, window, maximum=False)


def ts_arg_max(values: pd.DataFrame, window: int) -> pd.DataFrame:
    return _arg_extreme(values, window, maximum=True)


def ts_scale(values: pd.DataFrame, window: int) -> pd.DataFrame:
    size = _window(window)
    minimum = values.rolling(size, min_periods=size).min()
    span = values.rolling(size, min_periods=size).max() - minimum
    return (values - minimum) / span.replace(0.0, np.nan)


def days_from_last_change(values: pd.DataFrame) -> pd.DataFrame:
    output = pd.DataFrame(np.nan, index=values.index, columns=values.columns, dtype=float)
    for column in values:
        previous = None
        elapsed = 0
        for position, value in enumerate(values[column].to_numpy(dtype=float)):
            if np.isnan(value):
                output.iloc[position, output.columns.get_loc(column)] = np.nan
                previous = None
                elapsed = 0
            elif previous is None or value != previous:
                elapsed = 0
                output.iloc[position, output.columns.get_loc(column)] = 0.0
                previous = value
            else:
                elapsed += 1
                output.iloc[position, output.columns.get_loc(column)] = float(elapsed)
    return output


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


def panel_group_neutralize(values: pd.DataFrame, groups: pd.DataFrame) -> pd.DataFrame:
    """Demean each daily cross-section within its point-in-time groups.

    Missing group labels stay missing.  A one-security group receives zero,
    which is the exact residual from demeaning that group and is not filled or
    replaced with a broader classification.
    """
    _aligned_pair(values, groups)
    output = pd.DataFrame(np.nan, index=values.index, columns=values.columns, dtype=float)
    for timestamp in values.index:
        row = values.loc[timestamp]
        labels = groups.loc[timestamp]
        valid = row.notna() & labels.notna()
        if not valid.any():
            continue
        means = row[valid].groupby(labels[valid], sort=False).transform("mean")
        output.loc[timestamp, valid] = row[valid] - means
    return output


def linear_decay(values: pd.DataFrame, window: int) -> pd.DataFrame:
    size = _window(window)
    weights = np.arange(1, size + 1, dtype=float)
    denominator = float(weights.sum())

    def weighted(array: np.ndarray) -> float:
        if np.isnan(array).any():
            return np.nan
        return float(np.dot(array, weights) / denominator)

    return values.rolling(size, min_periods=size).apply(weighted, raw=True)
