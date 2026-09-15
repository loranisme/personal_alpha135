# V5 Lite Personal Alpha Library and Daily Selection Design

## 1. Product goal

V5 turns the completed V4 reconstruction into a personal, reviewable stock-selection product:

```text
BRAIN Source Registry (886 sources and hypotheses)
  -> V4 Base Alpha Library (135 COMPUTE_VERIFIED local formulas)
  -> Alpha Catalog
  -> manually approved Active Alpha Pool
  -> Current Liquid US Universe (500-1000 current securities)
  -> daily active-factor calculation
  -> cross-sectional percentile ranks
  -> equal-weight composite
  -> stock ranking
  -> Top 10% equal-weight target portfolio
  -> manual rebalance draft
  -> append-only forward paper log
```

This is a personal decision-support workflow. It produces rankings, targets and drafts for human review. It never submits, cancels or replaces broker orders.

## 2. Current baseline

- `reconstruction-v4` remains the immutable base library.
- The source registry contains 886 BRAIN Alpha IDs. These are source hypotheses, not 886 independent local formulas.
- V4 contains 135 deduplicated formulas associated with 317 BRAIN sources; 569 sources remain deferred.
- All 135 formulas are `COMPUTE_VERIFIED` and `DIAGNOSTIC_ONLY`.
- 120 formulas support both `tiingo_eod` and `alpaca_sip`.
- Of those 120 formulas, 84 have a classified primary mechanism and qualify for the default human-review candidate set; 36 remain visible but are excluded as `MECHANISM_UNCLASSIFIED`.
- 15 formulas require `vwap` and currently support only `alpaca_sip`.
- The existing three-factor/50-security sample proves the engineering chain. Its history is survivorship-biased and remains a regression fixture.
- `live_enabled=false` remains unchanged.

## 3. Scope

### Included

1. A readable Alpha Catalog derived deterministically from V4.
2. A structural shortlist and an editable human review workbook.
3. A frozen, hashed Active Pool containing 6-8 factors.
4. A current, liquid U.S. universe with 500-1000 securities.
5. Generic active-pool computation and equal-weight cross-sectional composite scoring.
6. Top-10% equal-weight portfolio targets with 5% minimum cash and 2% single-name cap.
7. Manual rebalance drafts based on user-supplied current positions and reference prices.
8. An append-only forward paper log with reproducible input hashes.
9. CSV, JSON and XLSX outputs for human review.

### Excluded

- ML feature generation, training, tuning, model artifacts or ML candidate registration.
- Automatic Research Admission, Validation, Holdout or E0-E4 promotion.
- Automatic factor-direction reversal based on historical returns.
- Full historical PIT universe certification.
- A new corporate-action engine or institutional OMS.
- Broker order submission, cancellation or replacement.
- IC/ICIR optimized weights, dynamic factor timing and advanced portfolio optimization.
- Marginal-contribution optimization and a full research leaderboard.

ML expansion is recorded only as a future idea. No V5 file or interface may depend on an ML module.

## 4. Canonical artifacts and immutability

The following files remain read-only inputs:

- `private/project_factor_library/CURRENT_LOCAL_ALPHA_LIBRARY.json`
- `private/project_factor_library/reconstruction-v4/project_factor_library.json`
- `private/project_factor_library/reconstruction-v4/manifest.json`

V5 writes new outputs under new run directories. It must not modify V4 formulas, directions, settings, source lineage, factor matrices or manifest.

Every V5 artifact carries:

- `library_version`
- V4 manifest SHA-256
- `as_of`
- upstream input hashes
- configuration hash
- `historical_pit_verified=false` unless a separately approved project changes that fact
- `live_orders_submitted=0`

## 5. Alpha Catalog contract

The catalog has exactly one row per V4 local formula and the following stable fields:

```text
local_factor_id
library_version
expression_hash
family_id
mechanism_tag
mechanism_status
semantic_status
required_fields
allowed_providers
default_provider_eligible
direction
direction_basis
build_status
usage_status
source_alpha_count
warmup_sessions
catalog_exclusion_reasons
```

Rules:

- The catalog must contain exactly 135 unique `local_factor_id` values.
- `direction=1` means the signed V4 expression is used unchanged. V5 never flips it from local return evidence.
- `usage_status=LIBRARY_ONLY` is the default.
- `default_provider_eligible=true` requires `tiingo_eod` support and no unresolved mechanism tag.
- All 15 VWAP-dependent Alpaca-only factors remain visible with `default_provider_eligible=false` and reason `ALPACA_VWAP_ONLY`.
- An unresolved tag is represented as `mechanism_tag=unclassified` and reason `MECHANISM_UNCLASSIFIED`.
- Provider and field lists are sorted before serialization.

## 6. Manual Active Pool contract

The system creates an `active_pool_review.xlsx` workbook with three sheets:

1. `Family Shortlist`: structurally eligible factors grouped by mechanism.
2. `Correlation Conflicts`: pairwise conflicts for the proposed factors when factor panels are available.
3. `Decision Template`: editable `decision` and `comment` columns.

The user enters one of:

```text
INCLUDE
WATCHLIST
REJECT
```

All IDs, formulas, directions, provider fields and computed metrics are read-only inputs. The freezer rejects changes to these columns.

The review workbook records the universe, date range, provider and input hash for every correlation or overlap matrix. The existing 50-stock sample may be used only for an initial redundancy screen and must be labeled `ENGINEERING_50`. It cannot support a claim about predictive power, portfolio performance or historical PIT validity.

An initial Active Pool is valid only when:

- it contains 6-8 `INCLUDE` rows;
- every factor is `COMPUTE_VERIFIED`;
- every factor is `default_provider_eligible=true`;
- every factor has `direction=1`;
- no two included factors share the same `family_id`;
- no two included factors share the same primary `mechanism_tag`;
- absolute score correlation above 0.80 or Top-10% overlap above 0.70 is resolved by retaining at most one conflicting factor;
- every included row has a non-empty human comment;
- weights are exactly `1 / N` and sum to one within numeric tolerance.

The initial shortlist uses no historical return ranking. Within a mechanism it sorts lexicographically by:

1. dual-provider availability;
2. higher cross-sectional coverage, when supplied;
3. semantic status (`FULL_INTENT` before `PARTIAL_INTENT`);
4. lower turnover, when supplied;
5. shorter warm-up;
6. `local_factor_id` as deterministic tie-breaker.

The frozen JSON contains the complete decision list, selected factor IDs, equal weights, review comments, selection basis, V4 manifest hash, data-exposure declaration and its own content hash. Changes create a new `active_pool_id`; files are never overwritten.

The initial pool fixes `provider=tiingo_eod`. Provider changes require a new Active Pool version.

## 7. Current Liquid US Universe contract

V5 builds a current selection universe rather than claiming a historical PIT universe.

Inputs:

- current U.S. asset metadata, including stable security ID, ticker, exchange, status, tradability and security type;
- at least 252 valid daily sessions per security;
- unadjusted close and volume for the last 60 valid sessions;
- current classification when available;
- source and availability timestamps.

Eligibility:

- exchange in NYSE, NASDAQ or NYSE American;
- common stock or REIT;
- exclude ADR, ETF, fund, preferred, warrant, unit and OTC;
- currently active and tradable;
- at least 252 valid sessions;
- latest unadjusted close at least USD 5;
- ADV60 at least USD 10 million.

All eligible securities are sorted by descending ADV60 and stable security ID. If more than 1000 qualify, retain the first 1000. If fewer than 500 qualify, return `BLOCKED_INSUFFICIENT_UNIVERSE` and do not create targets.

The universe is a current as-of snapshot. Every historical diagnostic that reuses it must carry `SURVIVORSHIP_BIASED_DIAGNOSTIC`. The V5 daily workflow never relabels it as PIT.

## 8. Daily factor and composite contract

- Compute only the factor IDs frozen in the Active Pool.
- Use `compute_registry_factors` and the provider bound in the Active Pool.
- SPY is available only for `benchmark_returns` and is removed from the stock cross-section.
- Each factor is ranked cross-sectionally with `rank(pct=True, method="average")`.
- A stock receives a composite score only when all Active Pool factors are finite for that stock.
- Composite score equals the arithmetic mean of the factor percentile ranks.
- Each active factor must cover at least 95% of the eligible universe.
- At least 500 stocks must have a complete composite score.
- Failure of either rule produces `BLOCKED_DATA` with zero target rows.
- Daily ranking can be generated every session. Target and rebalance files are generated only when `WEEKLY_FIRST_SESSION` is due.

## 9. Portfolio and manual draft contract

- Select the highest-scoring 10% of complete, eligible securities using `ceil(M * 0.10)`.
- Tie-break equal scores with stable security ID.
- Reserve at least 5% cash.
- Equal-weight selected stocks within the 95% invested budget. The per-stock target weight is fixed as `0.95 / ceil(M * 0.10)` before sector selection.
- Target single-name weight cannot exceed 2%.
- When current classifications are complete, walk the stable ranking and accept a stock only if its fixed target weight keeps its sector at or below 25%; continue until the required Top-10% count is filled. If the count cannot be filled, block the portfolio. Unresolved sector constraints produce a visible warning and retain `PAPER_ONLY` status.
- Convert target dollars to whole shares using supplied positive reference prices.
- Rebalance draft equals target shares minus user-supplied current shares.
- The output column is `suggested_trade_shares`; no `approved_trade_shares` or broker order object is produced.
- Every row has `review_status=REVIEW_REQUIRED`.
- Missing/invalid price, stale account snapshot or missing held security creates an explicit blocker.

Required tables:

```text
data_checks.csv
alpha_scores.parquet
stock_ranking.csv
target_portfolio.csv
manual_rebalance_draft.csv
blocked_items.csv
selection_review.xlsx
```

## 10. Forward Paper Log contract

The log is append-only and idempotent by `signal_id`.

At signal creation it records:

- `signal_id`, signal session and cutoff;
- Active Pool ID and hash;
- universe ID and hash;
- provider and raw-data hashes;
- ranked stock list, targets and rebalance-draft hash;
- status and blockers;
- `live_orders_submitted=0`.

When the five-session forward reference becomes available, a separate settlement event records reference entry/exit prices, total return, missing reason and settlement timestamp. Existing signal rows are not overwritten.

The paper log measures operational history. It cannot promote a factor to research validated, released or live-enabled status.

## 11. Acceptance

V5 Lite is accepted when:

1. Catalog generation accounts for all 135 formulas exactly once.
2. The 15 Alpaca-only VWAP factors and unresolved mechanism tags are visible but excluded from the default pool.
3. Human decisions freeze into a non-overwritable, hashed 6-8 factor Active Pool.
4. A 499-stock eligible input blocks; a 500-stock input runs; a 1001-stock input deterministically keeps 1000.
5. Only Active Pool factors are computed and SPY never enters the ranking.
6. Composite coverage failure creates no target portfolio.
7. A valid run creates Top-10% equal-weight targets and a manual draft with `REVIEW_REQUIRED` status.
8. No code path calls a broker order/cancel endpoint.
9. Forward paper events are append-only and duplicate signal IDs are rejected.
10. Existing V4 sample tests and the full repository regression suite remain green.
