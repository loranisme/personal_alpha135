# V5.1 Lite Library-First Alpha Filter Design

## 1. Decision

V5.1 keeps the personal Alpha Library as the product core. Alpha filtering,
composite construction and stock selection are small downstream capabilities
used to check whether factors are useful and to turn a frozen subset into a
reviewable ranking.

The system must remain suitable for one person to understand and operate. It
does not become a general research platform, an automated research-admission
system or a broker execution system.

This document supersedes the Alpha Catalog, Active Pool selection and composite
sections of `2026-09-15-v5-lite-alpha-selection-design.md`. The existing
universe, target-portfolio, optional manual-helper and paper-log contracts stay
in force unless this document explicitly changes them.

```text
BRAIN Source Registry
        |
        v
Immutable V4 Base Library (135 COMPUTE_VERIFIED formulas)
        +
Append-only Expansion Registry (future human / transformed / ML candidates)
        |
        v
Compute Verification
        |
        v
Unified Alpha Catalog
        |
        v
On-demand V5.1 Lite Alpha Filter
        |
        v
Proposed Active Pool -> Human Review -> Frozen Active Pool (5-12 factors)
        |
        v
Daily Active-Pool Calculation -> Equal-Weight Composite -> Stock Ranking
        |
        v
Top 10% Target Portfolio -> Optional Manual Rebalance Draft -> Forward Paper Log
```

## 2. Product boundaries

### Included

1. Preserve V4 as the immutable 135-factor base library.
2. Define an append-only registry contract for future Alpha expansion.
3. Build a unified Alpha Catalog from all `COMPUTE_VERIFIED` factor versions.
4. Run a compact, deterministic Alpha Filter only when requested or when the
   library/policy changes.
5. Produce a proposed Active Pool, require human review, then freeze a hashed
   Active Pool containing 5-12 factors.
6. Keep flat equal-weight rank aggregation as the only production composite.
7. Reuse the existing daily ranking, Top-10% portfolio, optional manual
   rebalance helper and append-only forward paper log.
8. Keep Alphalens and vectorbt as optional validation/reporting adapters.

### Excluded

- ML training or inference;
- automatic factor generation or formula mutation;
- automatic direction reversal;
- IC-, return- or optimizer-derived production weights;
- rolling-IC metric libraries, leave-one-out deletion loops or walk-forward
  research platforms;
- automatic Research Admission, Holdout or maturity promotion;
- new PIT fundamentals, historical PIT universe or corporate-action engines;
- broker order submission, cancellation or replacement;
- portfolio hysteresis, rank buffers and turnover optimization.

Future ML work may register candidate factors, but V5.1 has no ML dependency.

## 3. Canonical library model

### 3.1 Immutable V4 base

The following inputs remain read-only:

```text
private/project_factor_library/CURRENT_LOCAL_ALPHA_LIBRARY.json
private/project_factor_library/reconstruction-v4/project_factor_library.json
private/project_factor_library/reconstruction-v4/manifest.json
```

V5.1 never changes a V4 formula, direction, setting, source lineage or hash.

### 3.2 Expansion Registry

New Alpha versions are additive records. Every record contains:

```text
factor_id
factor_version
expression
expression_hash
economic_hypothesis
mechanism_tag
required_fields
allowed_providers
settings
direction
parent_factor_ids
generation_method
created_at
supersedes_factor_id
compute_status
lineage_hash
content_hash
```

Allowed `generation_method` values are initially:

```text
HUMAN_AUTHORED
FORMULA_TRANSFORM
IMPORTED_SOURCE
```

An ML-generated method may be added only in a separately approved version.

Allowed lifecycle states are:

```text
REGISTERED
COMPUTE_VERIFIED
COMPUTE_FAILED
SUPERSEDED
RETIRED
```

Only `COMPUTE_VERIFIED` versions enter the default filter candidate set.
Changing a formula creates a new factor version and content hash. No existing
record is overwritten.

### 3.3 Unified Alpha Catalog

The Catalog combines the V4 base and eligible Expansion Registry versions. It
contains one row per factor version and preserves:

```text
factor_id
factor_version
source_kind
family_id
mechanism_tag
mechanism_status
semantic_status
required_fields
allowed_providers
default_provider_eligible
direction
build_status
warmup_sessions
lineage_hash
content_hash
```

For V4, `local_factor_id` is used as `factor_id` and `factor_version` is the
frozen V4 version. `mechanism_tag` comes from the existing V5 Alpha Catalog,
joined by factor ID. Runtime mechanism inference is forbidden.

Unclassified and provider-ineligible records stay visible with an explicit
reason. They are not silently dropped.

## 4. When the Alpha Filter runs

The Alpha Filter is not part of the daily stock-selection path. It runs only
when one of the following occurs:

- the user explicitly requests an evaluation;
- a new factor version becomes `COMPUTE_VERIFIED`;
- filter policy or provider changes;
- the user wants to propose a new Active Pool.

Daily operation loads one already frozen Active Pool and calculates only those
factor IDs. A daily run must never reevaluate the whole library.

## 5. Frozen evaluation contract

### 5.1 Frequency

Primary factor evaluation uses weekly portfolio signal dates:

```text
primary_ic_frequency = WEEKLY_REBALANCE_SIGNAL
```

For a portfolio executing on the first valid trading session of a week, the
signal date is the immediately preceding valid trading session. Daily IC is
optional display-only evidence and cannot determine pool inclusion.

### 5.2 Forward label

The primary five-session label is:

```text
t close freezes the signal
t+1 open is the entry reference
t+6 open is the exit reference

forward_return_5d = open[t+6] / open[t+1] - 1
```

Offsets use exchange sessions. The same label is used for individual factor
IC, quantiles, composite comparison and forward-paper settlement. SPY never
enters the stock cross-section.

### 5.3 Evidence status

The existing 2019-2026 current-universe history remains:

```text
selection_evidence = DEVELOPMENT_EXPOSED
historical_pit_verified = false
independent_oos_claim_allowed = false
survivorship_bias = true
```

It may choose a provisional pool and establish an engineering baseline. It
cannot provide independent OOS validation. Clean forward evidence starts only
after the Active Pool and policy are frozen. Any pool, threshold, label or
composite change creates a new ID and restarts the forward-evidence clock.

## 6. Minimal Alpha diagnostics

Every catalog record receives exactly one filter status. Every evaluable
candidate receives this compact metric set:

```text
factor_id
factor_version
family_id
mechanism_tag
valid_signal_sessions
median_cross_section_coverage
mean_rank_ic_5d
icir_5d
positive_year_ratio
q1_mean_5d
q5_mean_5d
q5_minus_q1_5d
weekly_top10_turnover
filter_status
filter_reason
```

ICIR is reported but does not add a separate sign gate because its sign is the
same as mean IC when its standard deviation is valid.

For each weekly signal date, factor values are converted to cross-sectional
percentile ranks using `rank(pct=True, method="average")`. Rank IC is the
cross-sectional Spearman correlation between the signed rank and the frozen
five-session forward return. ICIR is not annualized.

`positive_year_ratio` uses only calendar years with at least 26 valid weekly
signal observations. A partial final year is eligible when it has at least 13
observations and is marked `PARTIAL_YEAR`.

Weekly Top-10% turnover is:

```text
1 - mean(|Top10[t] intersect Top10[t-1]| / |Top10[t-1]|)
```

Missing or invalid evidence remains missing and produces an explicit status.

## 7. Sequential filter gates

Thresholds live in one versioned configuration file and are never fitted by
searching historical portfolio return.

The provider is an explicit run-config value. Provider eligibility is evaluated
against the Catalog, and the old/new composite comparison must use the same
provider, universe, sessions and label contract.

```yaml
alpha_filter:
  primary_frequency: WEEKLY_REBALANCE_SIGNAL
  forward_label: T_CLOSE_T1_OPEN_T6_OPEN
  min_median_coverage: 0.95
  min_valid_signal_sessions: 100
  require_positive_mean_rank_ic: true
  require_positive_q5_q1: true
  min_positive_year_ratio: 0.60
  max_weekly_top10_turnover: 0.80
  score_corr_conflict: 0.80
  top10_overlap_conflict: 0.70
  active_pool_min: 5
  active_pool_max: 12
```

The gates run in this order:

1. **Data:** coverage and valid weekly sessions. Failure is `REJECT_DATA`.
2. **Signal:** positive mean signed Rank IC and positive Q5-Q1. Failure is
   `REJECT_SIGNAL`.
3. **Stability:** positive-year ratio at least 0.60. Failure is
   `WATCHLIST_UNSTABLE`.
4. **Usability:** weekly Top-10% turnover at most 0.80. Failure is
   `WATCHLIST_HIGH_TURNOVER`.

Watchlist records remain in the library and Catalog. They are excluded from
the default Active Pool proposal. No factor sign is automatically flipped.

## 8. Lightweight redundancy resolution

Redundancy analysis runs only on factors passing all four gates.

A hard pairwise conflict exists when any condition holds:

```text
same family_id
absolute median weekly cross-sectional score correlation > 0.80
mean weekly Top-10% Jaccard overlap > 0.70
```

Same `mechanism_tag` emits a concentration warning and is not a hard conflict.

To keep the result deterministic without building a clustering platform,
candidates are sorted lexicographically by:

1. higher `positive_year_ratio`;
2. higher `icir_5d`;
3. higher `mean_rank_ic_5d`;
4. higher `q5_minus_q1_5d`;
5. lower `weekly_top10_turnover`;
6. higher coverage;
7. `FULL_INTENT` before `PARTIAL_INTENT`;
8. shorter warm-up;
9. factor ID as tie-break.

The filter walks that order and accepts a factor when it has no hard conflict
with an already accepted factor. A rejected factor receives
`REJECT_REDUNDANT_WITH_<retained_factor_id>`. This avoids transitive
connected-component over-pruning and remains reproducible.

The Filter may produce any number of passing factors. It does not weaken gates
or add weak factors to meet a target count.

## 9. Proposed and frozen Active Pools

The Filter writes a `proposed_active_pool.json`. It does not overwrite or
activate a pool automatically.

The production composite is eligible only when 5-12 non-redundant factors are
approved. If fewer than five pass, individual diagnostics still complete and
the proposal returns:

```text
COMPOSITE_BLOCKED_INSUFFICIENT_ALPHA
```

If more than 12 pass, the proposal uses the first 12 in the deterministic
ordering. Human review may reject a proposed factor, but it may not insert a
factor that failed a hard gate without creating a separately labeled manual
experimental pool.

Freezing writes a new immutable Active Pool bundle containing:

```text
active_pool_id
selected_factor_ids and versions
equal factor weights
filter_run_id
selection_config_hash
V4 manifest hash
Expansion Registry snapshot hash
Alpha Catalog hash
provider
universe hash
sample start and end
selection evidence
historical_pit_verified
active_pool_frozen_at
review decision and comments
content hash
```

Every selected weight equals `1/N` and weights sum to one. Pool files are never
overwritten.

## 10. Composite and stock-selection path

The sole production composite is flat equal weight over cross-sectional factor
ranks:

```text
Composite[j,t] = mean(RankPct[i,j,t] for selected factor i)
```

There are no fitted weights. Mechanism concentration is reported as a warning.
A mechanism-equal-weight composite may be calculated during one-off analysis,
but remains an optional diagnostic and is not persisted as a second production
method.

The current portfolio path remains unchanged:

```text
Frozen Active Pool
  -> current eligible universe
  -> active-factor ranks
  -> flat equal-weight composite
  -> full stock ranking
  -> Top 10%
  -> equal-weight target with 5% cash
  -> optional REVIEW_REQUIRED manual rebalance draft
  -> append-only forward paper log
```

No broker order or cancellation endpoint is called.

## 11. Validation adapters

Alphalens and vectorbt remain optional adapters invoked by an explicit sample
or review run.

- Alphalens reports IC and quantile diagnostics under the frozen label.
- vectorbt reports turnover, modeled cost and portfolio behavior.
- Portfolio return, CAGR and Sharpe never determine factor admission.
- The frozen existing eight-factor composite remains
  `BASELINE_V5_8_FACTOR` for like-for-like comparison.

The minimum baseline comparison is:

```text
Mean Rank IC 5D
ICIR 5D
Positive Year Ratio
Q5-Q1
Weekly Top10 Turnover
Total Return
Sharpe
Maximum Drawdown
Modeled Fees
```

## 12. Outputs

Each filter execution writes a new immutable run directory:

```text
private/runs/v5-alpha-filter/<run_id>/
```

Required artifacts are limited to:

```text
alpha_filter_config.yaml
alpha_filter_manifest.json
alpha_filter_diagnostics.csv
alpha_yearly_ic.csv
alpha_redundancy_conflicts.csv
alpha_filter_decisions.csv
proposed_active_pool.json
composite_diagnostics.csv
baseline_comparison.csv
blocked_items.csv
alpha_filter_review.xlsx
```

Large score panels and pairwise matrices may be cached internally using
content-addressed files. They are not required reader-facing deliverables.

The workbook has five sheets:

```text
Summary
Alpha Diagnostics
Selected Pool
Composite Comparison
Audit
```

The Audit sheet states the label timing, weekly frequency, provider, universe,
current-universe survivorship bias, development exposure, configuration and
input hashes, no direction reversal and `live_orders_submitted=0`.

## 13. Failure handling

- Missing inputs remain missing; they are not filled with plausible zeros.
- Provider-ineligible factors receive `NOT_EVALUATED_PROVIDER`.
- Unclassified factors remain visible and receive
  `NOT_ELIGIBLE_UNCLASSIFIED_MECHANISM` for the default pool.
- A catalog/library/hash mismatch stops the run before diagnostics.
- Existing non-empty run directories are never overwritten.
- Fewer than five passing factors block only composite creation, not library
  registration or individual diagnostic delivery.
- A failed optional Alphalens/vectorbt adapter cannot corrupt the Catalog,
  Filter decisions or frozen Active Pool.

## 14. Acceptance criteria

1. All V4 factors and all registered expansion versions are accounted for
   exactly once in the Catalog.
2. V4 formulas, directions, settings and hashes remain unchanged.
3. Expansion records are append-only and content-hashed.
4. Only `COMPUTE_VERIFIED` candidate versions enter the default filter.
5. Mechanism tags come from the Catalog and are never inferred at runtime.
6. Primary IC uses only weekly rebalance signal sessions.
7. The 5D label is exactly `t close -> t+1 open -> t+6 open`.
8. SPY never enters the stock cross-section.
9. Negative historical IC never reverses factor direction.
10. Every candidate receives one explicit status and reason.
11. Data and signal failures cannot enter a standard pool proposal.
12. Unstable and high-turnover factors remain visible but are excluded from the
    default proposal.
13. Same-family, score-correlation and Top-10% overlap conflicts are resolved
    deterministically.
14. Same-mechanism factors may coexist when numerically non-redundant.
15. Identical inputs and configuration produce identical decisions and hashes.
16. The Filter never weakens gates to reach a target factor count.
17. A production proposal contains 5-12 factors or explicitly blocks composite
    creation.
18. Production composite weights are flat equal weights and sum to one.
19. Daily selection evaluates only the frozen Active Pool.
20. Existing eight-factor baseline remains available.
21. Historical selection stays `DEVELOPMENT_EXPOSED` and survivorship-biased.
22. New pools restart the append-only forward-evidence clock.
23. No broker order/cancel endpoint is called and
    `live_orders_submitted=0`.
24. Existing V4 and V5 regression tests remain green.

## 15. Implementation sequence

Implementation stops after these steps:

```text
P0.1  Add Expansion Registry contracts and append-only validation
P0.2  Build unified Catalog snapshots from V4 plus verified expansions
P0.3  Freeze weekly signal and open-to-open five-session labels
P0.4  Compute minimal diagnostics for all eligible candidates
P0.5  Apply Data, Signal, Stability and Usability gates
P0.6  Compute conflicts and perform deterministic greedy deduplication
P0.7  Write proposed pool and human-review workbook
P0.8  Freeze a reviewed 5-12 factor Active Pool
P0.9  Build flat equal-weight composite and compare with the V5 baseline
P0.10 Reuse daily selection and forward paper log; add hashes and tests
```

ML, factor transformation, portfolio turnover controls and additional research
metrics require separate designs and approval.

## 16. Success definition

V5.1 succeeds when the project can continually register new factor versions,
verify their computability, filter them through one compact and reproducible
check, freeze a small understandable Active Pool and use that pool for daily
stock ranking. Historical performance may remain weak; the system must report
that result without tuning the filter or composite to manufacture an attractive
backtest.
