# V5 Lite Personal Alpha Library and Daily Selection Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn the immutable V4 library into a configuration-driven personal stock-selection workflow that creates an Alpha Catalog, a human-approved Active Pool, a current 500-1000 stock universe, daily rankings, target portfolios, an optional manual rebalance helper and an append-only forward paper log.

**Architecture:** V4 remains a read-only base. New focused modules build catalog and pool metadata, filter a current liquid universe, calculate only frozen active factors, generate equal-weight Top-10% targets, and record paper events. A separate optional helper converts target weights into share differences when account inputs are supplied. The main path contains no broker transport or ML dependency.

**Tech Stack:** Python 3.12, pandas, pyarrow, openpyxl, exchange-calendars, existing Alphalens/vectorbt dependencies for optional diagnostics, pytest, JSON/CSV/Parquet/XLSX/SQLite.

**Spec:** `docs/superpowers/specs/2026-09-15-v5-lite-alpha-selection-design.md`

## Global Constraints

- Never modify `private/project_factor_library/reconstruction-v4/**`.
- Preserve all 135 V4 signed expressions with `direction=1`; never infer a flip from historical performance.
- Default Active Pool candidates must support `tiingo_eod`; 15 Alpaca-only VWAP factors stay in the catalog but are not default candidates.
- Active Pool size is 6-8. Duplicate `family_id` is blocked; duplicate primary mechanism emits `SAME_MECHANISM_INCLUDED` without blocking.
- Current universe contains 500-1000 securities; it is never labeled historical PIT.
- Composite uses equal-weight cross-sectional percentile ranks and requires every active score per stock.
- Portfolio uses Top 10%, 5% minimum cash, 2% single-name cap and equal weight.
- Historical metrics in the catalog review view are explicitly `DIAGNOSTIC_ONLY` and never drive direction, automatic ordering or pool validity.
- Rankings and target weights are core outputs. The manual rebalance execution helper is optional; no broker order/cancel transport is permitted.
- ML modules, model artifacts and ML candidate schemas are outside this plan.
- Preserve `live_enabled=false`, `DIAGNOSTIC_ONLY` and existing R2 governance code.
- Every task uses red-green TDD and ends with a focused commit.

---

### Task 1: Build the deterministic V4 Alpha Catalog

**Files:**
- Create: `src/us_equity_alpha/alpha_catalog.py`
- Create: `tests/test_alpha_catalog.py`
- Modify: `src/us_equity_alpha/proxy_converter.py`
- Modify: `src/us_equity_alpha/cli.py`
- Create: `config/alpha_catalog_policy.json`

**Interfaces:**
- Consumes: `Mapping[str, Any]` loaded from V4 `project_factor_library.json`; V4 manifest SHA-256; optional diagnostic summary with an explicit evidence scope.
- Produces: `required_history_sessions(expression, settings) -> int`, `build_alpha_catalog(library, policy, *, library_version) -> pandas.DataFrame`, `build_catalog_review_view(catalog, diagnostics) -> pandas.DataFrame` and `write_alpha_catalog(...) -> dict[str, Path]`.
- CLI: `us-equity-alpha build-alpha-catalog --library PATH --manifest PATH --policy PATH [--diagnostics PATH] --output DIR`.

- [ ] **Step 1: Write catalog contract tests**

```python
def test_catalog_accounts_for_every_formula_and_preserves_signed_direction():
    frame = build_alpha_catalog(sample_library(135), catalog_policy(), library_version="reconstruction-v4")
    assert len(frame) == frame.local_factor_id.nunique() == 135
    assert set(frame.direction) == {1}
    assert set(frame.build_status) == {"COMPUTE_VERIFIED"}


def test_vwap_and_unclassified_factors_remain_visible_but_not_default_eligible():
    frame = build_alpha_catalog(library_with_vwap_and_unclassified(), catalog_policy(), library_version="reconstruction-v4")
    vwap = frame.set_index("local_factor_id").loc["vwap_factor"]
    unknown = frame.set_index("local_factor_id").loc["unknown_factor"]
    assert not vwap.default_provider_eligible
    assert "ALPACA_VWAP_ONLY" in vwap.catalog_exclusion_reasons
    assert not unknown.default_provider_eligible
    assert "MECHANISM_UNCLASSIFIED" in unknown.catalog_exclusion_reasons


def test_catalog_uses_expression_delay_and_decay_for_required_history():
    factor = one_factor_library(
        expression="ts_mean(close, 20)",
        settings={"delay": 1, "decay": 4},
    )
    frame = build_alpha_catalog(factor, catalog_policy(), library_version="reconstruction-v4")
    assert frame.loc[0, "warmup_sessions"] == 23


def test_diagnostic_view_is_labeled_and_cannot_change_catalog_decisions():
    catalog = build_alpha_catalog(sample_library(135), catalog_policy(), library_version="reconstruction-v4")
    view = build_catalog_review_view(catalog, diagnostic_fixture())
    assert set(view.diagnostic_status.dropna()) == {"DIAGNOSTIC_ONLY"}
    assert view.default_provider_eligible.equals(catalog.default_provider_eligible)
    assert view.direction.equals(catalog.direction)
    assert view.usage_status.equals(catalog.usage_status)


def test_diagnostic_view_rejects_unapproved_performance_columns():
    diagnostics = diagnostic_fixture().assign(sharpe=1.5)
    with pytest.raises(ValueError, match="INVALID_DIAGNOSTIC_SCHEMA"):
        build_catalog_review_view(catalog_fixture(), diagnostics)
```

- [ ] **Step 2: Run the new test and confirm the missing-module failure**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/test_alpha_catalog.py -q`

Expected: collection fails with `ModuleNotFoundError: us_equity_alpha.alpha_catalog`.

- [ ] **Step 3: Expose the existing history requirement calculation**

Add a public wrapper in `proxy_converter.py` around the existing `_node_required_history` logic. It parses the signed local expression, adds factor delay and the extra decay observations, and raises `INVALID_FACTOR_EXPRESSION` for syntax it cannot parse.

```python
def required_history_sessions(expression, settings):
    try:
        tree = ast.parse(expression, mode="exec")
    except SyntaxError as exc:
        raise ValueError("INVALID_FACTOR_EXPRESSION") from exc
    return (
        _node_required_history(tree)
        + int(settings.get("delay", 0))
        + max(int(settings.get("decay", 0)) - 1, 0)
    )
```

- [ ] **Step 4: Implement normalized catalog rows**

```python
CATALOG_COLUMNS = (
    "local_factor_id", "library_version", "expression_hash", "family_id",
    "mechanism_tag", "mechanism_status", "semantic_status", "required_fields",
    "allowed_providers", "default_provider_eligible", "direction",
    "direction_basis", "build_status", "usage_status", "source_alpha_count",
    "warmup_sessions", "catalog_exclusion_reasons",
)


def build_alpha_catalog(library, policy, *, library_version):
    if library_version != "reconstruction-v4":
        raise ValueError("UNSUPPORTED_BASE_LIBRARY_VERSION")
    rows = []
    for factor in library["factors"]:
        tags = factor.get("economic_description", {}).get("mechanism_tags", [])
        tag = tags[0] if tags else "unclassified"
        providers = sorted(factor.get("allowed_providers", []))
        fields = sorted(factor.get("field_bindings", {}))
        reasons = []
        if "tiingo_eod" not in providers:
            reasons.append("ALPACA_VWAP_ONLY" if "vwap" in fields else "DEFAULT_PROVIDER_UNAVAILABLE")
        if tag == "unclassified":
            reasons.append("MECHANISM_UNCLASSIFIED")
        if factor.get("build_status") != "COMPUTE_VERIFIED":
            reasons.append("NOT_COMPUTE_VERIFIED")
        rows.append({
            "local_factor_id": factor["local_factor_id"],
            "library_version": library_version,
            "expression_hash": factor["expression_hash"],
            "family_id": factor["family_id"],
            "mechanism_tag": tag,
            "mechanism_status": "CLASSIFIED" if tag != "unclassified" else "UNCLASSIFIED",
            "semantic_status": factor["semantic_status"],
            "required_fields": ",".join(fields),
            "allowed_providers": ",".join(providers),
            "default_provider_eligible": not reasons,
            "direction": 1,
            "direction_basis": factor["direction_basis"],
            "build_status": factor["build_status"],
            "usage_status": "LIBRARY_ONLY",
            "source_alpha_count": len(factor.get("source_alpha_ids", [])),
            "warmup_sessions": required_history_sessions(factor["expression"], factor["settings"]),
            "catalog_exclusion_reasons": ",".join(reasons),
        })
    result = pd.DataFrame(rows, columns=CATALOG_COLUMNS).sort_values("local_factor_id")
    if len(result) != result.local_factor_id.nunique():
        raise ValueError("DUPLICATE_LOCAL_FACTOR_ID")
    return result.reset_index(drop=True)
```

- [ ] **Step 5: Implement the optional diagnostic-only catalog view**

Accept only these values: five-session mean Rank IC, five-session Rank ICIR, five-session Top-minus-Bottom net spread, and mean turnover. Require `diagnostic_status=DIAGNOSTIC_ONLY`, sample start/end, universe label, provider, cost-model hash and input hash for every populated row. Preserve missing values with a reason. Reject Sharpe, annualized return, maximum drawdown or any unknown metric column. Join diagnostics into a separate review DataFrame without mutating the structural catalog.

```python
DIAGNOSTIC_VALUE_COLUMNS = (
    "diagnostic_rank_ic_mean_5d",
    "diagnostic_rank_icir_5d",
    "diagnostic_top_bottom_net_spread_5d",
    "diagnostic_turnover_mean",
)
DIAGNOSTIC_EVIDENCE_COLUMNS = (
    "diagnostic_status",
    "diagnostic_sample_start",
    "diagnostic_sample_end",
    "diagnostic_universe_label",
    "diagnostic_provider",
    "diagnostic_cost_model_hash",
    "diagnostic_input_hash",
    "diagnostic_missing_reason",
)
```

- [ ] **Step 6: Implement canonical output and CLI dispatch**

Write `alpha_catalog.csv`, `alpha_catalog.parquet`, `alpha_catalog_review.csv`, `catalog_summary.json` and `manifest.json`. When diagnostics are absent, the review view retains empty diagnostic columns with `diagnostic_missing_reason=NOT_SUPPLIED`. Hash the input library manifest, diagnostics when supplied, policy and every output. Refuse a non-empty output directory. Add the CLI parser and a dispatch branch that returns exit code 2 for validation errors.

- [ ] **Step 7: Run focused tests**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/test_alpha_catalog.py tests/test_factor_library.py tests/test_contracts.py -q`

Expected: all selected tests pass.

- [ ] **Step 8: Commit Task 1**

```bash
git add src/us_equity_alpha/alpha_catalog.py src/us_equity_alpha/proxy_converter.py src/us_equity_alpha/cli.py config/alpha_catalog_policy.json tests/test_alpha_catalog.py
git commit -m "Add deterministic V4 alpha catalog"
```

### Task 2: Generate and freeze the human-reviewed Active Pool

**Files:**
- Create: `src/us_equity_alpha/active_pool.py`
- Create: `tests/test_active_pool.py`
- Modify: `src/us_equity_alpha/cli.py`
- Create: `config/active_pool_policy.json`

**Interfaces:**
- Consumes: Alpha Catalog review DataFrame, structural coverage with the exact columns `local_factor_id,coverage`, optional factor-score panels, editable `Decision Template` sheet and V4 manifest hash.
- Produces: `build_active_pool_review(catalog, coverage, scores, policy, output_path) -> dict`, `freeze_active_pool(review_path, library, manifest_sha256, policy, output_path) -> dict`.
- CLI: `build-active-pool-review` and `freeze-active-pool`.

- [ ] **Step 1: Write candidate-list and freeze rejection tests**

```python
def test_candidate_list_keeps_all_eligible_rows_and_ranks_within_mechanism():
    review = build_review_candidates(catalog_fixture(), coverage_fixture(), pool_policy())
    assert set(review.local_factor_id) == set(catalog_fixture().query("default_provider_eligible").local_factor_id)
    assert review.groupby("mechanism_tag").mechanism_rank.min().eq(1).all()


def test_diagnostic_values_never_change_candidate_order():
    first = build_review_candidates(catalog_fixture(), coverage_fixture(), pool_policy())
    changed = catalog_fixture().assign(
        diagnostic_rank_ic_mean_5d=lambda x: -x.diagnostic_rank_ic_mean_5d,
        diagnostic_top_bottom_net_spread_5d=lambda x: -x.diagnostic_top_bottom_net_spread_5d,
    )
    second = build_review_candidates(changed, coverage_fixture(), pool_policy())
    assert first.local_factor_id.tolist() == second.local_factor_id.tolist()


@pytest.mark.parametrize("mutation,error", [
    ("five_factors", "ACTIVE_POOL_SIZE"),
    ("duplicate_family", "DUPLICATE_FAMILY"),
    ("changed_direction", "DIRECTION_MUST_REMAIN_ONE"),
    ("missing_comment", "INCLUDE_COMMENT_REQUIRED"),
    ("vwap_factor", "FACTOR_NOT_DEFAULT_PROVIDER_ELIGIBLE"),
    ("high_correlation_pair", "UNRESOLVED_FACTOR_CONFLICT"),
])
def test_freezer_rejects_invalid_human_decisions(mutation, error, tmp_path):
    review = decision_workbook(mutation, tmp_path)
    with pytest.raises(ValueError, match=error):
        freeze_active_pool(review, sample_library(), "a" * 64, pool_policy(), tmp_path / "pool.json")


def test_same_mechanism_is_a_visible_warning_not_a_blocker(tmp_path):
    review = decision_workbook("same_mechanism", tmp_path)
    result = freeze_active_pool(review, sample_library(), "a" * 64, pool_policy(), tmp_path / "pool.json")
    assert result["status"] == "FROZEN_WITH_WARNINGS"
    warning = next(item for item in result["warnings"] if item["code"] == "SAME_MECHANISM_INCLUDED")
    assert len(warning["factor_ids"]) >= 2
```

- [ ] **Step 2: Run the new test and confirm the missing-module failure**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/test_active_pool.py -q`

Expected: collection fails with `ModuleNotFoundError: us_equity_alpha.active_pool`.

- [ ] **Step 3: Implement structural candidate ordering**

```python
def build_review_candidates(catalog, coverage, policy):
    eligible = catalog[catalog.default_provider_eligible].copy()
    if set(coverage.columns) != {"local_factor_id", "coverage"}:
        raise ValueError("INVALID_COVERAGE_SCHEMA")
    eligible = eligible.merge(coverage, on="local_factor_id", how="left", validate="one_to_one")
    eligible["dual_provider"] = eligible.allowed_providers.str.contains("alpaca_sip") & eligible.allowed_providers.str.contains("tiingo_eod")
    eligible["semantic_order"] = eligible.semantic_status.map({"FULL_INTENT": 0, "PARTIAL_INTENT": 1}).fillna(2)
    eligible = eligible.sort_values(
        ["mechanism_tag", "dual_provider", "coverage", "semantic_order", "warmup_sessions", "local_factor_id"],
        ascending=[True, False, False, True, True, True],
        kind="stable",
    )
    eligible["mechanism_rank"] = eligible.groupby("mechanism_tag", sort=False).cumcount() + 1
    eligible["structural_recommendation"] = eligible.mechanism_rank.eq(1)
    return eligible.reset_index(drop=True)
```

The candidate list contains every default-eligible factor. It uses provider availability, coverage, semantic status, warm-up and stable ID for ordering. Diagnostic Rank IC, Rank ICIR, spread and turnover remain visible read-only columns but never affect ordering or validity. Pairwise score correlation and Top-10% overlap are calculated from the separately supplied score panels and written to `Correlation Conflicts`. Each workbook records the score-panel universe, date range, provider and input hash. A score panel from the existing 50-stock engineering sample may support redundancy review only and must be labeled `ENGINEERING_50`; it is not evidence of prediction, portfolio performance or historical PIT validity.

- [ ] **Step 4: Write the review workbook**

Create the exact sheets `Family Shortlist`, `Correlation Conflicts`, and `Decision Template`. Protect structural columns in the first two sheets. In `Decision Template`, add data validation containing `INCLUDE,WATCHLIST,REJECT`; allow edits only to `decision` and `comment`. Reopen the workbook with openpyxl and assert sheet names, row counts and validation rules before returning success.

- [ ] **Step 5: Implement immutable Active Pool freezing**

Read the workbook twice: once for the structural snapshot and once for decisions. Validate 6-8 includes, unique factor/family, default-provider eligibility, unchanged direction and non-empty comments. For included pairs, reject an unresolved conflict when absolute score correlation exceeds 0.80 or Top-10% overlap exceeds 0.70. If multiple included factors share a primary mechanism, add `SAME_MECHANISM_INCLUDED` and the affected IDs to `warnings` while allowing the freeze. Reject an existing output path. Set `provider=tiingo_eod`, assign equal `Decimal` weights, serialize with sorted keys, compute `content_sha256` excluding that field, then persist it.

- [ ] **Step 6: Run focused tests**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/test_active_pool.py tests/test_alpha_catalog.py -q`

Expected: all selected tests pass.

- [ ] **Step 7: Commit Task 2**

```bash
git add src/us_equity_alpha/active_pool.py src/us_equity_alpha/cli.py config/active_pool_policy.json tests/test_active_pool.py
git commit -m "Add human-reviewed active alpha pools"
```

### Task 3: Build the current 500-1000 security liquid universe

**Files:**
- Create: `src/us_equity_alpha/current_universe.py`
- Create: `tests/test_current_universe.py`
- Create: `scripts/download_current_universe.py`
- Modify: `src/us_equity_alpha/alpaca_data.py`
- Modify: `src/us_equity_alpha/cli.py`

**Interfaces:**
- Consumes: current asset metadata DataFrame, `dict[str, DataFrame]` unadjusted daily bars, timezone-aware as-of, optional current classification DataFrame.
- Produces: `build_current_liquid_universe(assets, bars, as_of, policy) -> tuple[pd.DataFrame, dict]` and a hashed snapshot directory.
- CLI: `build-current-universe --assets PATH --bars DIR --classifications PATH --as-of ISO8601 --output DIR`.

- [ ] **Step 1: Write exact universe boundary tests**

```python
def test_universe_blocks_499_accepts_500_and_caps_1001_at_1000():
    with pytest.raises(ValueError, match="BLOCKED_INSUFFICIENT_UNIVERSE"):
        build_current_liquid_universe(*universe_inputs(499), as_of=cutoff(), policy=policy())
    accepted, evidence = build_current_liquid_universe(*universe_inputs(500), as_of=cutoff(), policy=policy())
    assert len(accepted) == evidence["eligible_count"] == 500
    capped, evidence = build_current_liquid_universe(*universe_inputs(1001), as_of=cutoff(), policy=policy())
    assert len(capped) == 1000
    assert capped.adv60.is_monotonic_decreasing


def test_current_snapshot_never_claims_historical_pit():
    _, evidence = build_current_liquid_universe(*universe_inputs(500), as_of=cutoff(), policy=policy())
    assert evidence["historical_pit_verified"] is False
    assert evidence["historical_reuse_label"] == "SURVIVORSHIP_BIASED_DIAGNOSTIC"
```

- [ ] **Step 2: Run the new test and confirm the missing-module failure**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/test_current_universe.py -q`

Expected: collection fails with `ModuleNotFoundError: us_equity_alpha.current_universe`.

- [ ] **Step 3: Implement asset and liquidity filters**

Reuse `security_master.liquidity_eligible` for the 252-session, USD 5 and ADV60 rules. Reject unknown security types, inactive assets, non-U.S. exchanges, ADR/ETF/fund/preferred/warrant/unit/OTC records, duplicate security IDs and bars without explicit source/availability metadata.

- [ ] **Step 4: Implement deterministic cap and evidence**

Sort eligible rows by `adv60 DESC, security_id ASC`, cap at 1000, and hash the canonical CSV bytes. Emit exclusion rows with one or more explicit reason codes, provider coverage, as-of timestamp, `historical_pit_verified=false` and `SURVIVORSHIP_BIASED_DIAGNOSTIC`.

- [ ] **Step 5: Add credential-safe snapshot acquisition**

Extend `alpaca_data.py` with a read-only current asset metadata request. `scripts/download_current_universe.py` reads Alpaca and Tiingo credentials only from environment variables, stores no headers or credentials, resumes per-symbol Tiingo EOD downloads, writes raw-response hashes, and stops without a partial eligible universe when fewer than 500 securities have complete input.

- [ ] **Step 6: Run focused tests**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/test_current_universe.py tests/test_security_master_policy_v2.py tests/test_alpaca_adapter.py tests/test_market_data.py -q`

Expected: all selected tests pass.

- [ ] **Step 7: Commit Task 3**

```bash
git add src/us_equity_alpha/current_universe.py src/us_equity_alpha/alpaca_data.py src/us_equity_alpha/cli.py scripts/download_current_universe.py tests/test_current_universe.py
git commit -m "Add current liquid US selection universe"
```

### Task 4: Generalize active-factor calculation and composite ranking

**Files:**
- Create: `src/us_equity_alpha/daily_selection.py`
- Create: `tests/test_daily_selection.py`
- Modify: `src/us_equity_alpha/factor_library.py`

**Interfaces:**
- Consumes: V4 library, frozen Active Pool JSON, provider field panels, current universe DataFrame and signal session.
- Produces: `calculate_active_scores(library, active_pool, provider, inputs, universe, session) -> SelectionScores` where `SelectionScores` contains raw panels, ranked panels, composite, coverage table and blockers.

- [ ] **Step 1: Write computation isolation and coverage tests**

```python
def test_only_active_factors_are_computed_and_spy_is_never_ranked(monkeypatch):
    result = calculate_active_scores(library(), pool_of_six(), "tiingo_eod", fields_with_spy(), universe_500(), session())
    assert set(result.factor_panels) == set(pool_of_six()["factor_ids"])
    assert "SPY" not in result.composite.index
    assert len(result.composite.dropna()) == 500


def test_incomplete_active_factor_coverage_blocks_targets():
    result = calculate_active_scores(library(), pool_of_six(), "tiingo_eod", fields_at_94_percent(), universe_500(), session())
    assert result.status == "BLOCKED_DATA"
    assert "ACTIVE_FACTOR_COVERAGE_BELOW_95_PERCENT" in result.blockers
```

- [ ] **Step 2: Run the new test and confirm the missing-module failure**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/test_daily_selection.py -q`

Expected: collection fails with `ModuleNotFoundError: us_equity_alpha.daily_selection`.

- [ ] **Step 3: Add a generic factor list adapter**

Keep the hard-coded V4 three-factor sample untouched. Add a generic adapter in `factor_library.py` that validates the Active Pool content hash, provider binding, equal weights and factor IDs, then calls `compute_registry_factors` only for those IDs.

- [ ] **Step 4: Implement percentile ranks and composite completeness**

```python
ranked = {
    factor_id: panel.loc[session, eligible_ids].rank(pct=True, method="average")
    for factor_id, panel in factor_panels.items()
}
rank_frame = pd.DataFrame(ranked)
complete = rank_frame.notna().all(axis=1)
composite = rank_frame.mean(axis=1).where(complete)
```

Calculate per-factor eligible coverage before applying the complete-row mask. Block below 95% factor coverage or below 500 complete scores. Stable-sort scores descending and security ID ascending.

- [ ] **Step 5: Run focused tests**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/test_daily_selection.py tests/test_factor_library.py tests/test_v4_workflow_sample.py -q`

Expected: all selected tests pass and the fixed V4 sample behavior remains unchanged.

- [ ] **Step 6: Commit Task 4**

```bash
git add src/us_equity_alpha/daily_selection.py src/us_equity_alpha/factor_library.py tests/test_daily_selection.py
git commit -m "Add active-pool daily composite calculation"
```

### Task 5: Produce Top-10% targets and the optional manual execution helper

**Files:**
- Create: `src/us_equity_alpha/manual_selection.py`
- Create: `tests/test_manual_selection.py`
- Create: `tests/test_reporting.py`
- Modify: `src/us_equity_alpha/reporting.py`
- Create: `config/personal_selection_policy.json`

**Interfaces:**
- Core consumes: ranked scores, eligible universe and selection policy.
- Optional helper consumes: completed target weights, current positions, reference prices, account NAV and helper policy.
- Produces: `build_personal_targets(ranking, universe, policy) -> dict[str, pd.DataFrame]`, `build_manual_rebalance_helper(targets, positions, prices, nav, policy) -> dict[str, pd.DataFrame]`, and `write_selection_review(bundle, output_dir) -> dict[str, Path]`.

- [ ] **Step 1: Write portfolio and no-order tests**

```python
def test_500_ranked_stocks_create_50_equal_weight_targets_and_cash_floor():
    bundle = build_personal_targets(ranking_500(), universe_500(), policy())
    stocks = bundle["target_portfolio"].query("security_id != 'CASH'")
    assert len(stocks) == 50
    assert stocks.target_weight.nunique() == 1
    assert stocks.target_weight.max() <= 0.02
    assert bundle["target_portfolio"].query("security_id == 'CASH'").target_weight.iloc[0] >= 0.05
    assert bundle["execution_helper_status"] == "NOT_REQUESTED"
    assert "manual_rebalance_draft" not in bundle


def test_manual_draft_contains_no_executable_order_fields():
    targets = build_personal_targets(ranking_500(), universe_500(), policy())
    helper = build_manual_rebalance_helper(targets["target_portfolio"], positions(), prices_500(), 100000, policy())
    draft = helper["manual_rebalance_draft"]
    assert set(draft.review_status) == {"REVIEW_REQUIRED"}
    assert "suggested_trade_shares" in draft
    assert "approved_trade_shares" not in draft
    assert "order_id" not in draft


def test_blocked_helper_does_not_remove_core_selection():
    targets = build_personal_targets(ranking_500(), universe_500(), policy())
    helper = build_manual_rebalance_helper(targets["target_portfolio"], stale_positions(), prices_500(), 100000, policy())
    assert helper["execution_helper_status"] == "BLOCKED_HELPER"
    assert len(targets["target_portfolio"]) == 51
```

- [ ] **Step 2: Run the new test and confirm the missing-module failure**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/test_manual_selection.py -q`

Expected: collection fails with `ModuleNotFoundError: us_equity_alpha.manual_selection`.

- [ ] **Step 3: Implement selection and weight targets**

Let `K = ceil(M * 0.10)` and fix the per-stock target weight at `0.95 / K`. Walk the stable score/security-ID ranking, accepting a stock only when its addition keeps its sector weight at or below 25%, until K stocks are selected. Block with `SECTOR_CAP_CANNOT_FILL_TARGET_COUNT` if K cannot be filled. Cap each target at 2% and add a 5% `CASH` row. The core target table ends at weights and requires no NAV, positions or share rounding. When current classification is incomplete, add `SECTOR_CONSTRAINT_UNAVAILABLE` and retain `PAPER_ONLY` without claiming the sector cap was enforced.

- [ ] **Step 4: Implement the optional manual rebalance helper**

Expose the helper separately from target construction. Convert weights to whole target shares using NAV and positive reference prices, outer-join target shares and current positions, preserve held securities absent from the new target with target zero, and calculate `suggested_trade_shares = target_shares - current_shares`. Reject stale account timestamps, missing held-security prices and non-integer positions into `BLOCKED_HELPER`. Set every successful row to `REVIEW_REQUIRED`. Helper blockers never delete or downgrade the core ranking and target-weight files.

- [ ] **Step 5: Write and reopen all delivery formats**

Always write the core tables plus `selection_review.xlsx`. Write `manual_rebalance_draft.csv` and its workbook sheet only when the helper succeeds; otherwise record `execution_helper_status=NOT_REQUESTED` or `BLOCKED_HELPER` in the manifest and checks sheet. Add `tests/test_reporting.py` with CSV/Parquet/XLSX row-count, schema, cash tie-out, conditional-sheet and workbook-to-machine-table equality cases. Reopen every output artifact and verify those same contracts before writing the manifest.

- [ ] **Step 6: Run focused tests**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/test_manual_selection.py tests/test_portfolio.py tests/test_reporting.py -q`

Expected: all selected tests pass.

- [ ] **Step 7: Commit Task 5**

```bash
git add src/us_equity_alpha/manual_selection.py src/us_equity_alpha/reporting.py config/personal_selection_policy.json tests/test_manual_selection.py tests/test_reporting.py
git commit -m "Add personal targets and optional rebalance helper"
```

### Task 6: Add the append-only Forward Paper Log

**Files:**
- Create: `src/us_equity_alpha/paper_log.py`
- Create: `tests/test_paper_log.py`

**Interfaces:**
- Consumes: completed selection bundle and later five-session reference-return observations.
- Produces: `PaperLog(path)`, `record_signal(event)`, `settle_signal(event)`, and `export() -> dict`.

- [ ] **Step 1: Write append-only and idempotency tests**

```python
def test_signal_and_settlement_are_separate_append_only_events(tmp_path):
    log = PaperLog(tmp_path / "paper.sqlite")
    log.record_signal(signal_event("s1"))
    log.settle_signal(settlement_event("s1"))
    exported = log.export()
    assert [row["kind"] for row in exported["events"]] == ["SIGNAL", "SETTLEMENT"]
    assert exported["events"][0]["payload"] == signal_event("s1")


def test_duplicate_signal_and_unknown_settlement_are_rejected(tmp_path):
    log = PaperLog(tmp_path / "paper.sqlite")
    log.record_signal(signal_event("s1"))
    with pytest.raises(ValueError, match="DUPLICATE_SIGNAL_ID"):
        log.record_signal(signal_event("s1"))
    with pytest.raises(ValueError, match="UNKNOWN_SIGNAL_ID"):
        log.settle_signal(settlement_event("missing"))
```

- [ ] **Step 2: Run the new test and confirm the missing-module failure**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/test_paper_log.py -q`

Expected: collection fails with `ModuleNotFoundError: us_equity_alpha.paper_log`.

- [ ] **Step 3: Implement the SQLite event journal**

Use a table with `sequence INTEGER PRIMARY KEY AUTOINCREMENT`, `kind`, `signal_id`, `payload`, `created_at`, and a unique partial index for `kind='SIGNAL'`. Validate that signal payloads include pool/universe/config/input hashes, blockers, target hash, `execution_helper_status`, optional helper-output hash and `live_orders_submitted=0`. A settlement requires an existing signal and records entry/exit reference, five-session total return or explicit missing reason.

- [ ] **Step 4: Implement deterministic export**

Export ordered JSON events and a flat `paper_summary.csv`. Do not calculate Research Admission, Validation or Release status. Preserve missing outcomes as missing with a reason.

- [ ] **Step 5: Run focused tests**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/test_paper_log.py tests/test_evidence.py -q`

Expected: all selected tests pass.

- [ ] **Step 6: Commit Task 6**

```bash
git add src/us_equity_alpha/paper_log.py tests/test_paper_log.py
git commit -m "Add append-only forward paper log"
```

### Task 7: Wire the V5 Lite CLI and run end-to-end acceptance

**Files:**
- Modify: `src/us_equity_alpha/cli.py`
- Modify: `src/us_equity_alpha/runtime.py`
- Create: `scripts/run_v5_lite_selection.py`
- Create: `tests/test_v5_lite_workflow.py`
- Modify: `README.md`

**Interfaces:**
- Core consumes: V4 library, Active Pool, current-universe snapshot and daily provider inputs.
- Optional helper consumes: `--execution-helper` together with positions, account and reference-price snapshots.
- Produces: one immutable V5 run directory and one appended signal event; the rebalance draft is conditional.
- CLI: `daily-select --library PATH --active-pool PATH --universe PATH --market-data DIR --as-of ISO8601 --output DIR --paper-log PATH [--execution-helper --positions PATH --account PATH --reference-prices PATH]`.

- [ ] **Step 1: Write end-to-end blocked and successful cases**

```python
def test_v5_blocks_without_500_complete_scores_and_writes_no_targets(tmp_path):
    result = run_v5_lite(v5_fixture(499), tmp_path / "run", tmp_path / "paper.sqlite")
    assert result["status"] == "BLOCKED_DATA"
    assert not (tmp_path / "run" / "target_portfolio.csv").exists()


def test_v5_valid_input_writes_review_artifacts_and_no_orders(tmp_path):
    result = run_v5_lite(v5_fixture(500), tmp_path / "run", tmp_path / "paper.sqlite")
    assert result["status"] == "SELECTION_READY"
    assert result["live_orders_submitted"] == 0
    assert result["execution_helper_status"] == "NOT_REQUESTED"
    assert (tmp_path / "run" / "stock_ranking.csv").is_file()
    assert (tmp_path / "run" / "target_portfolio.csv").is_file()
    assert not (tmp_path / "run" / "manual_rebalance_draft.csv").exists()
    assert (tmp_path / "run" / "selection_review.xlsx").is_file()


def test_v5_requested_helper_adds_review_only_draft(tmp_path):
    result = run_v5_lite(v5_fixture(500, execution_helper=True), tmp_path / "run", tmp_path / "paper.sqlite")
    assert result["status"] == "SELECTION_READY"
    assert result["execution_helper_status"] == "EXECUTION_HELPER_READY"
    assert result["live_orders_submitted"] == 0
    assert (tmp_path / "run" / "manual_rebalance_draft.csv").is_file()
```

- [ ] **Step 2: Run the new test and confirm the missing-entrypoint failure**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/test_v5_lite_workflow.py -q`

Expected: fails because `run_v5_lite` and the CLI command do not exist.

- [ ] **Step 3: Implement the orchestration script**

Load and hash every supplied input before calculation. Verify V4 and Active Pool manifests, create the current universe, calculate active scores, block before targets on coverage failure, and always write ranking and target weights on core success. Invoke the execution helper only when the flag and all three helper inputs are present. Record `NOT_REQUESTED`, `EXECUTION_HELPER_READY` or `BLOCKED_HELPER`, append the paper signal, then write a self-verifying run manifest. Refuse a pre-existing output directory.

- [ ] **Step 4: Add CLI error mapping**

Map invalid core configuration to exit 2, unavailable/insufficient core data to exit 4, and successful selection to exit 0. Incomplete helper arguments are invalid configuration. Valid but stale or missing helper data yields `BLOCKED_HELPER` while preserving exit 0 and core selection artifacts. All payloads include stable status codes and `live_orders_submitted=0`.

- [ ] **Step 5: Document the operator workflow**

Update README with the exact command sequence: build the structural and diagnostic-only catalog views, generate the review workbook, freeze the approved pool, build the current universe, run core daily selection, optionally request the manual rebalance helper, and settle paper observations. State that ML expansion and historical PIT certification are outside V5 Lite.

- [ ] **Step 6: Run V5 and regression verification**

Run:

```bash
PYTHONPATH=src .venv/bin/python -m pytest tests/test_alpha_catalog.py tests/test_active_pool.py tests/test_current_universe.py tests/test_daily_selection.py tests/test_manual_selection.py tests/test_paper_log.py tests/test_v5_lite_workflow.py -q
PYTHONPATH=src .venv/bin/python -m pytest -q --disable-warnings
git diff --check
```

Expected: all V5 tests and the entire pre-existing regression suite pass; `git diff --check` exits 0.

- [ ] **Step 7: Run artifact and credential QA**

Generate one synthetic 500-security core run and one helper-enabled run. Reopen their CSV/Parquet/XLSX/SQLite outputs, recompute every manifest hash, scan tracked and delivered files for credential patterns, and assert `live_orders_submitted=0` plus absence of broker order IDs. Verify that the core-only run has no rebalance-draft file.

- [ ] **Step 8: Commit Task 7**

```bash
git add src/us_equity_alpha/cli.py src/us_equity_alpha/runtime.py scripts/run_v5_lite_selection.py tests/test_v5_lite_workflow.py README.md
git commit -m "Complete V5 Lite personal selection workflow"
```

## Final review checklist

- [ ] V4 manifest and all 135 formulas remain byte-identical.
- [ ] Catalog counts are 135 total, 120 Tiingo-capable formulas, 84 default-eligible classified candidates, 36 Tiingo-capable but unclassified entries and 15 Alpaca-only VWAP entries.
- [ ] The Active Pool is human-approved, 6-8 factors, equal weight and hash-frozen; repeated mechanisms are visible warnings while duplicate families still block.
- [ ] The catalog review view exposes only the four approved diagnostic metrics, with evidence scope and `DIAGNOSTIC_ONLY`, and changing them cannot change automated selection behavior.
- [ ] Current-universe evidence says `historical_pit_verified=false`.
- [ ] Fewer than 500 complete stocks blocks targets.
- [ ] SPY is absent from factor cross-sectional ranks and portfolio rows.
- [ ] Core selection needs no account inputs; the optional helper never changes core target weights.
- [ ] Every helper line requires manual review and no broker transport exists.
- [ ] Forward Paper Log is append-only and does not promote research/release state.
- [ ] ML code, configuration and dependencies are absent from the V5 implementation.
- [ ] Full regression, manifest verification and credential scan pass before completion.
