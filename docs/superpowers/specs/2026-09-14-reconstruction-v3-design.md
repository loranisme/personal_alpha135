# Reconstruction V3 Design

## Objective

Reprocess the frozen 886-source BRAIN registry without performance inputs, exhaust the defensible local OHLCV/SPY conversion surface, and write a new immutable `reconstruction-v3` library. `reconstruction-v2` remains unchanged evidence.

## Frozen boundaries

- Source count remains 886 and every source retains a card and decision.
- Local formulas are independent `LOCAL_RECONSTRUCTION` candidates; BRAIN value parity and inherited performance are not claimed.
- New candidates remain `DIAGNOSTIC_ONLY`; research eligibility and release counts remain zero.
- Missing fundamental, analyst, option, event-news, social and proprietary observables are not replaced with price momentum.
- Only explicit non-negative additive ranked components may be separated from an unavailable composite.
- The local missing-value policy propagates missing values. FASTEXPR `filter=true` is removed only with an explicit loss marker.
- Description/formula conflicts retain both tracks: the described hypothesis stays unresolved and the measurable formula becomes `RELATED_NEW_HYPOTHESIS`.
- `ts_regression(..., rettype=2)` remains deferred until authoritative operator semantics are available.
- Historical PIT, portfolio, execution, cost, company-action, evidence and release gates do not change.

## Capability additions

The capability catalog may derive the following from verified provider bars:

- `news_close_vol := volume`
- true range `TR := max(high-low, abs(high-delay(close,1)), abs(low-delay(close,1)))`
- `news_atr14 := ts_mean(TR,14)`
- `news_atr_ratio := (high-low)/ts_mean(TR,20)`
- SPY return from the same provider's raw close panel
- rolling beta and correlation against SPY for 30/60/90/360-session registered windows
- systematic risk as `abs(beta_W) * ts_std_dev(SPY_return,W)`
- unsystematic risk as the rolling standard deviation of `stock_return - beta_W*SPY_return`

Each non-identity binding records `SOURCE_FIELD_PARITY_NOT_CLAIMED:<field>`, raw-price limitations, provider scope and `historical_pit_verified=false`.

## Converter corrections

Substantiveness is assessed on the translated local AST, recognizing price arithmetic, `ts_mean`, `ts_sum`, covariance/correlation and volatility operations. Variadic `add(..., filter=true)` becomes a normal additive AST and records that source filter semantics were removed. Approved outer wrappers may be discarded only when extracting an otherwise valid explicit additive ranked component, with a wrapper-loss marker.

## Verification overlay

Verification accepts the original provider bundles plus an optional long-history Tiingo raw-bar snapshot. A later overlay may promote a `COMPILED` factor to `COMPUTE_VERIFIED`; it may not demote an existing pass merely because another provider is inapplicable. Missing long-history SPY leaves benchmark-dependent factors `COMPILED`.

The current 2020-2025 50-security cache is engineering evidence only. It remains survivorship-biased and cannot satisfy the R2 minimum 500-security coverage or PIT requirements.

## Reporting contract

V3 reports separately:

- sources with cards;
- sources associated with any local candidate;
- source lineages classed `FULL_INTENT`, `PARTIAL_INTENT`, or `RELATED_NEW_HYPOTHESIS`;
- unique formulas and structural families;
- compute-verified, compiled and engineering-failed formulas and source associations;
- still-deferred sources and their blockers;
- provider/sample scope and manifest hashes.

The expected conversion scope is the existing 246 associations plus up to 72 defensible additional associations. Counts after deterministic deduplication and execution are authoritative; the design estimate is not an acceptance shortcut.

## Acceptance

- All 886 sources remain accounted for exactly once.
- The seven non-benchmark long-window formulas pass on the existing long-history cache.
- Benchmark-dependent long-window formulas remain compiled until matching long-history SPY is supplied.
- No performance field enters conversion or verification.
- Prefix invariance passes for every compute-verified matrix.
- V2 remains byte-for-byte unchanged.
- All artifacts are written to a new directory with a self-verifying manifest.
