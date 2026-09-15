"""Reviewed local market observables derived only from verified bar panels."""

from __future__ import annotations

import copy

import pandas as pd


RETURN = "close / ts_delay(close, 1) - 1"
BENCHMARK = "benchmark_returns"
TRUE_RANGE = (
    "max(max(high - low, abs(high - ts_delay(close, 1))), "
    "abs(low - ts_delay(close, 1)))"
)


def _capability(capability_id, binding, providers, scope, definition, semantic_status):
    return {
        "capability_id": capability_id,
        "binding": binding,
        "providers": sorted(providers),
        "status": "DERIVED_REQUIRES_QA",
        "definition": definition,
        "coverage_scope": {provider: copy.deepcopy(scope.get(provider, {})) for provider in providers},
        "historical_pit_verified": False,
        "semantic_status": semantic_status,
        "local_formula_authorized": True,
        "source_value_parity": "NOT_VERIFIED",
    }


def add_local_market_capabilities(inputs, capabilities):
    """Add performance-blind price/volume and SPY-risk capability bindings."""
    augmented = {provider: dict(fields) for provider, fields in inputs.items()}
    observed = copy.deepcopy(capabilities)
    scope = {
        provider: observed.get("close", {}).get("coverage_scope", {}).get(provider, {})
        for provider in augmented
    }

    volume_providers = [p for p, fields in augmented.items() if "volume" in fields]
    observed["news_close_vol"] = _capability(
        "local_main_session_share_volume",
        "volume",
        volume_providers,
        scope,
        "Observed main-session share volume; source field value parity is not verified.",
        "RELATED_NEW_HYPOTHESIS",
    )
    range_providers = [
        p for p, fields in augmented.items() if {"high", "low", "close"}.issubset(fields)
    ]
    observed["news_atr14"] = _capability(
        "local_raw_price_atr14",
        f"ts_mean({TRUE_RANGE}, 14)",
        range_providers,
        scope,
        "14-session mean raw-price true range.",
        "PARTIAL_INTENT",
    )
    observed["news_atr_ratio"] = _capability(
        "local_raw_price_range_to_atr20",
        f"(high - low) / ts_mean({TRUE_RANGE}, 20)",
        range_providers,
        scope,
        "Raw daily range divided by 20-session mean raw-price true range.",
        "PARTIAL_INTENT",
    )

    benchmark_providers = []
    for provider, fields in augmented.items():
        close = fields.get("close")
        if close is None or "SPY" not in close.columns:
            continue
        spy = close["SPY"] / close["SPY"].shift(1) - 1
        security_columns = [column for column in close.columns if column != "SPY"]
        for field_name, panel in list(fields.items()):
            if isinstance(panel, pd.DataFrame) and "SPY" in panel.columns:
                fields[field_name] = panel.drop(columns="SPY")
        fields[BENCHMARK] = pd.DataFrame(
            {column: spy for column in security_columns}, index=close.index
        )
        benchmark_providers.append(provider)
    if not benchmark_providers:
        return augmented, observed

    observed[BENCHMARK] = _capability(
        "local_spy_raw_price_return",
        BENCHMARK,
        benchmark_providers,
        scope,
        "Raw SPY close-to-close price return replicated across the provider panel.",
        "PARTIAL_INTENT",
    )
    beta = {}
    for window in (30, 60, 90, 360):
        beta[window] = (
            f"ts_covariance({RETURN}, {BENCHMARK}, {window}) / "
            f"ts_covariance({BENCHMARK}, {BENCHMARK}, {window})"
        )
        observed[f"beta_last_{window}_days_spy"] = _capability(
            f"local_rolling_beta_spy_{window}", beta[window], benchmark_providers, scope,
            f"{window}-session raw-return covariance/variance beta against SPY.",
            "PARTIAL_INTENT",
        )
    for window in (30, 90, 360):
        observed[f"correlation_last_{window}_days_spy"] = _capability(
            f"local_rolling_correlation_spy_{window}",
            f"ts_corr({RETURN}, {BENCHMARK}, {window})",
            benchmark_providers,
            scope,
            f"{window}-session raw-return correlation against SPY.",
            "PARTIAL_INTENT",
        )
    for window in (30, 60, 90, 360):
        observed[f"systematic_risk_last_{window}_days"] = _capability(
            f"local_systematic_risk_spy_{window}",
            f"abs({beta[window]}) * ts_std_dev({BENCHMARK}, {window})",
            benchmark_providers,
            scope,
            f"Absolute rolling SPY beta times {window}-session SPY raw-return volatility.",
            "RELATED_NEW_HYPOTHESIS",
        )
        observed[f"unsystematic_risk_last_{window}_days"] = _capability(
            f"local_residual_risk_spy_{window}",
            f"ts_std_dev(({RETURN}) - ({beta[window]}) * {BENCHMARK}, {window})",
            benchmark_providers,
            scope,
            f"{window}-session volatility of residual raw returns after rolling SPY beta.",
            "RELATED_NEW_HYPOTHESIS",
        )
    return augmented, observed
