# V5.1 Lite Library-First Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an append-only expansion registry and a compact, deterministic Alpha Filter that proposes a reviewable 5-12 factor equal-weight Active Pool without turning the project into a research platform.

**Architecture:** Extend the immutable V4 catalog with independently hashed expansion records, then evaluate eligible factor versions on frozen weekly signal dates and a trade-aligned five-session label. Sequential gates and deterministic greedy conflict resolution produce a proposed pool; a separate review/freeze step emits an Active Pool compatible with the existing daily selection and paper-log path.

**Tech Stack:** Python 3.12, pandas, NumPy, SciPy, PyArrow, exchange-calendars, Alphalens Reloaded, vectorbt, openpyxl for repository runtime reports, pytest, JSON/CSV/Parquet artifacts.

**Spec:** `docs/superpowers/specs/2026-09-16-v5-1-lite-library-first-design.md`

## Global Constraints

- V4 formulas, settings, direction, lineage and hashes remain immutable.
- Primary evaluation frequency is `WEEKLY_REBALANCE_SIGNAL`.
- The label is `t close -> t+1 open -> t+6 open` using exchange sessions.
- SPY is benchmark-only and never enters factor ranks or stock quantiles.
- Current 2019-2026 evidence is `DEVELOPMENT_EXPOSED`, survivorship-biased and not independent OOS.
- The default production Active Pool has 5-12 factors; the filter never weakens gates to reach five.
- Production composite weights are flat equal weights; no IC or return weighting is allowed.
- Same family, absolute score correlation above 0.80 and Top-10% overlap above 0.70 are hard conflicts.
- Same mechanism is a warning, not a hard conflict.
- No broker order/cancel endpoint is called; every artifact records `live_orders_submitted=0`.
- Daily selection computes only one frozen Active Pool.
- Every run and pool is immutable and content-hashed.

---

### Task 1: Append-only Expansion Registry

**Files:**
- Create: `src/us_equity_alpha/expansion_registry.py`
- Create: `tests/test_expansion_registry.py`
- Modify: `src/us_equity_alpha/cli.py`

**Interfaces:**
- Produces: `register_expansion(definition: Mapping[str, Any], registry_dir: Path | str) -> dict[str, Any]`
- Produces: `load_expansion_snapshot(registry_dir: Path | str) -> list[dict[str, Any]]`
- Produces CLI command: `register-expansion --definition PATH --registry PATH`
- Consumes no mutable V4 data.

- [ ] **Step 1: Write failing registry tests**

```python
def test_register_expansion_is_append_only_and_hashed(tmp_path):
    result = register_expansion(_definition("candidate-a", 1), tmp_path)
    assert result["compute_status"] == "REGISTERED"
    assert result["content_hash"] == canonical_hash_without_content_hash(result)
    with pytest.raises(FileExistsError, match="EXPANSION_VERSION_EXISTS"):
        register_expansion(_definition("candidate-a", 1), tmp_path)


def test_changed_expression_requires_new_version(tmp_path):
    register_expansion(_definition("candidate-a", 1), tmp_path)
    changed = _definition("candidate-a", 1) | {"expression": "rank(close)"}
    with pytest.raises(FileExistsError, match="EXPANSION_VERSION_EXISTS"):
        register_expansion(changed, tmp_path)
    result = register_expansion(changed | {"factor_version": 2}, tmp_path)
    assert result["factor_version"] == 2


def test_snapshot_rejects_tampered_records(tmp_path):
    path = Path(register_expansion(_definition("candidate-a", 1), tmp_path)["path"])
    payload = json.loads(path.read_text())
    payload["expression"] = "-close"
    path.write_text(json.dumps(payload))
    with pytest.raises(ValueError, match="EXPANSION_CONTENT_HASH_MISMATCH"):
        load_expansion_snapshot(tmp_path)
```

- [ ] **Step 2: Verify the tests fail**

Run: `PYTHONPATH=src .venv/bin/pytest -q tests/test_expansion_registry.py`

Expected: collection fails because `us_equity_alpha.expansion_registry` does not exist.

- [ ] **Step 3: Implement strict record normalization and append-only writes**

Implement:

```python
REQUIRED_FIELDS = {
    "factor_id", "factor_version", "expression", "economic_hypothesis",
    "mechanism_tag", "required_fields", "allowed_providers", "settings",
    "direction", "parent_factor_ids", "generation_method", "created_at",
    "supersedes_factor_id", "compute_status",
}

ALLOWED_GENERATION_METHODS = {
    "HUMAN_AUTHORED", "FORMULA_TRANSFORM", "IMPORTED_SOURCE"
}

ALLOWED_COMPUTE_STATUSES = {
    "REGISTERED", "COMPUTE_VERIFIED", "COMPUTE_FAILED", "SUPERSEDED", "RETIRED"
}
```

Normalize sorted fields/providers/parents, require `direction == 1`, calculate
`expression_hash`, `lineage_hash` and `content_hash`, and write with mode `x`
to `<registry>/<factor_id>/v<factor_version>.json`.

- [ ] **Step 4: Add the registration CLI**

Parse a JSON definition, call `register_expansion`, emit the normalized record
metadata and `live_orders_submitted=0`, and map validation failures to
`BLOCKED_CONFIG` with exit code 2.

- [ ] **Step 5: Run focused tests**

Run: `PYTHONPATH=src .venv/bin/pytest -q tests/test_expansion_registry.py tests/test_runtime_cli.py`

Expected: all tests pass.

- [ ] **Step 6: Commit**

```bash
git add src/us_equity_alpha/expansion_registry.py src/us_equity_alpha/cli.py tests/test_expansion_registry.py
git commit -m "Add append-only alpha expansion registry"
```

---

### Task 2: Unified Catalog Snapshot

**Files:**
- Modify: `src/us_equity_alpha/alpha_catalog.py`
- Create: `tests/test_unified_alpha_catalog.py`
- Modify: `src/us_equity_alpha/cli.py`

**Interfaces:**
- Consumes: `load_expansion_snapshot(...) -> list[dict[str, Any]]`
- Produces: `build_unified_catalog(v4_library, v5_catalog, expansions, provider) -> pd.DataFrame`
- Produces: `write_unified_catalog(..., output_dir) -> dict[str, Path]`
- Produces CLI command: `build-unified-catalog`.

- [ ] **Step 1: Write failing unified-catalog tests**

```python
def test_unified_catalog_keeps_every_v4_and_expansion_version_once():
    result = build_unified_catalog(_v4_library(), _v5_catalog(), [_verified_expansion()], "tiingo_eod")
    assert len(result) == 136
    assert result[["factor_id", "factor_version"]].duplicated().sum() == 0
    assert set(result.source_kind) == {"V4_BASE", "EXPANSION"}


def test_only_verified_expansions_are_filter_eligible():
    result = build_unified_catalog(
        _v4_library(), _v5_catalog(), [_expansion("REGISTERED"), _expansion("COMPUTE_VERIFIED", version=2)], "tiingo_eod"
    )
    rows = result[result.source_kind.eq("EXPANSION")].set_index("factor_version")
    assert not rows.loc[1, "filter_eligible"]
    assert rows.loc[2, "filter_eligible"]


def test_v4_mechanism_comes_from_catalog_not_runtime_inference():
    result = build_unified_catalog(_v4_library(), _v5_catalog(mechanism="catalog-tag"), [], "tiingo_eod")
    assert result.iloc[0].mechanism_tag == "catalog-tag"
```

- [ ] **Step 2: Verify the tests fail**

Run: `PYTHONPATH=src .venv/bin/pytest -q tests/test_unified_alpha_catalog.py`

Expected: import fails for `build_unified_catalog`.

- [ ] **Step 3: Implement catalog normalization**

Add stable columns for `factor_id`, `factor_version`, `source_kind`, provider
eligibility, filter eligibility, lineage/content hashes and exclusion reasons.
Require an exact one-to-one V4-to-V5-catalog join and reject unknown or missing
V4 IDs. Expansion mechanism tags come from their registered definitions.

- [ ] **Step 4: Implement immutable bundle writer and CLI**

Write `unified_alpha_catalog.csv`, `.parquet`, `catalog_summary.json` and a
SHA-256 manifest into an empty directory. The CLI accepts `--library`,
`--base-catalog`, `--expansion-registry`, `--provider` and `--output`.

- [ ] **Step 5: Run catalog regression tests**

Run: `PYTHONPATH=src .venv/bin/pytest -q tests/test_alpha_catalog.py tests/test_unified_alpha_catalog.py`

Expected: all tests pass and the existing 135-row V4 catalog behavior remains unchanged.

- [ ] **Step 6: Commit**

```bash
git add src/us_equity_alpha/alpha_catalog.py src/us_equity_alpha/cli.py tests/test_unified_alpha_catalog.py
git commit -m "Build unified base and expansion alpha catalog"
```

---

### Task 3: Weekly Trade-Aligned Diagnostic Engine

**Files:**
- Create: `src/us_equity_alpha/alpha_filter_diagnostics.py`
- Create: `tests/test_alpha_filter_diagnostics.py`

**Interfaces:**
- Produces: `weekly_signal_sessions(sessions: pd.DatetimeIndex) -> list[pd.Timestamp]`
- Produces: `forward_open_returns(opens: pd.DataFrame, signal_sessions: Sequence[pd.Timestamp], horizon: int = 5) -> pd.DataFrame`
- Produces: `diagnose_factor(ranks: pd.DataFrame, forward_returns: pd.DataFrame, config: Mapping[str, Any]) -> tuple[dict[str, Any], pd.DataFrame]`
- Produces: `weekly_top10_turnover(ranks: pd.DataFrame) -> float | None`.

- [ ] **Step 1: Write failing timing and metric tests**

```python
def test_forward_label_uses_t1_open_and_t6_open():
    sessions = pd.bdate_range("2026-01-02", periods=8)
    opens = pd.DataFrame({"A": range(100, 108)}, index=sessions)
    result = forward_open_returns(opens, [sessions[0]], horizon=5)
    assert result.loc[sessions[0], "A"] == pytest.approx(106 / 101 - 1)


def test_spy_is_excluded_before_cross_sectional_metrics():
    ranks = _rank_panel(columns=["A", "B", "SPY"])
    returns = _return_panel(columns=["A", "B", "SPY"])
    with pytest.raises(ValueError, match="BENCHMARK_IN_FACTOR_CROSS_SECTION"):
        diagnose_factor(ranks, returns, _config())


def test_diagnostics_report_weekly_ic_years_quantiles_and_turnover():
    metrics, yearly = diagnose_factor(_predictive_ranks(), _aligned_returns(), _config())
    assert metrics["mean_rank_ic_5d"] > 0
    assert metrics["q5_minus_q1_5d"] > 0
    assert 0 <= metrics["weekly_top10_turnover"] <= 1
    assert set(yearly.columns) == {"year", "valid_signal_sessions", "mean_rank_ic_5d", "year_status"}
```

- [ ] **Step 2: Verify the tests fail**

Run: `PYTHONPATH=src .venv/bin/pytest -q tests/test_alpha_filter_diagnostics.py`

Expected: import fails for the new module.

- [ ] **Step 3: Implement weekly signal and label contracts**

Reuse the XNYS-compatible weekly-first-session convention already represented
by `sample_workflow.weekly_rebalance_dates`. Require strictly increasing,
unique sessions and return NaN when entry/exit prices are unavailable.

- [ ] **Step 4: Implement minimal diagnostics**

Calculate weekly cross-sectional percentile ranks, Spearman Rank IC, unannualized
ICIR, calendar-year IC with 26-observation full-year and 13-observation partial-
year rules, Q1/Q5 mean returns, Q5-Q1 and weekly Top-10% retention/turnover.
Preserve missing values instead of substituting zero.

- [ ] **Step 5: Run focused tests**

Run: `PYTHONPATH=src .venv/bin/pytest -q tests/test_alpha_filter_diagnostics.py tests/test_backtest.py`

Expected: all tests pass.

- [ ] **Step 6: Commit**

```bash
git add src/us_equity_alpha/alpha_filter_diagnostics.py tests/test_alpha_filter_diagnostics.py
git commit -m "Add weekly trade-aligned alpha diagnostics"
```

---

### Task 4: Sequential Gates and Deterministic Redundancy Filter

**Files:**
- Create: `src/us_equity_alpha/alpha_filter.py`
- Create: `tests/test_alpha_filter.py`
- Create: `config/v5_1_alpha_filter.json`

**Interfaces:**
- Produces: `apply_filter_gates(diagnostics: pd.DataFrame, config: Mapping[str, Any]) -> pd.DataFrame`
- Produces: `compute_redundancy_conflicts(candidates, rank_panels, config) -> pd.DataFrame`
- Produces: `greedy_nonredundant_selection(candidates, conflicts, config) -> tuple[pd.DataFrame, list[str]]`
- Produces: `build_proposed_pool(selected, run_metadata, config) -> dict[str, Any]`.

- [ ] **Step 1: Write failing gate tests**

```python
def test_gates_do_not_flip_negative_factor():
    result = apply_filter_gates(_diagnostics(mean_ic=-0.01, qspread=-0.02), _policy())
    assert result.iloc[0].filter_status == "REJECT_SIGNAL"
    assert result.iloc[0].direction == 1


def test_filter_does_not_pad_to_minimum():
    selected, blockers = greedy_nonredundant_selection(_four_passing_candidates(), _no_conflicts(), _policy())
    assert len(selected) == 4
    assert blockers == ["COMPOSITE_BLOCKED_INSUFFICIENT_ALPHA"]


def test_same_mechanism_is_warning_not_conflict():
    selected, blockers = greedy_nonredundant_selection(_same_mechanism_candidates(), _no_conflicts(), _policy())
    assert len(selected) == 5
    assert "SAME_MECHANISM_INCLUDED" in blockers
```

- [ ] **Step 2: Write failing deterministic conflict tests**

```python
def test_conflicts_reject_lower_priority_factor_deterministically():
    decisions, blockers = greedy_nonredundant_selection(_ordered_candidates(), _conflict("F1", "F2"), _policy())
    row = decisions.set_index("factor_id").loc["F2"]
    assert row.filter_status == "REJECT_REDUNDANT"
    assert row.filter_reason == "REJECT_REDUNDANT_WITH_F1"


def test_identical_inputs_produce_identical_proposed_pool_hash():
    first = build_proposed_pool(_five_selected(), _metadata(), _policy())
    second = build_proposed_pool(_five_selected(), _metadata(), _policy())
    assert first == second
```

- [ ] **Step 3: Verify the tests fail**

Run: `PYTHONPATH=src .venv/bin/pytest -q tests/test_alpha_filter.py`

Expected: import fails for the new module.

- [ ] **Step 4: Implement gates and stable ordering**

Implement the four sequential gates with statuses from the spec. Sort passing
candidates by positive-year ratio, ICIR, mean IC, Q-spread, inverse turnover,
coverage, semantic status, warmup and factor ID. Do not use a weighted score.

- [ ] **Step 5: Implement pairwise conflicts and greedy acceptance**

Compute median weekly cross-sectional Spearman correlation and mean weekly
Top-10% Jaccard overlap only for gate-passing candidates. Add same-family hard
edges. Walk sorted candidates and reject each candidate conflicting with an
already accepted factor; cap accepted factors at 12 without weakening gates.

- [ ] **Step 6: Add frozen default policy**

Write `config/v5_1_alpha_filter.json` with the exact thresholds in the design,
schema version 1, evidence label `DEVELOPMENT_EXPOSED` and
`historical_pit_verified=false`.

- [ ] **Step 7: Run focused tests**

Run: `PYTHONPATH=src .venv/bin/pytest -q tests/test_alpha_filter.py`

Expected: all tests pass.

- [ ] **Step 8: Commit**

```bash
git add src/us_equity_alpha/alpha_filter.py config/v5_1_alpha_filter.json tests/test_alpha_filter.py
git commit -m "Add deterministic lightweight alpha filter"
```

---

### Task 5: Filter Run, Review, and Compatible Active Pool Freeze

**Files:**
- Create: `src/us_equity_alpha/alpha_filter_workflow.py`
- Create: `tests/test_alpha_filter_workflow.py`
- Modify: `src/us_equity_alpha/daily_selection.py`
- Modify: `src/us_equity_alpha/cli.py`

**Interfaces:**
- Produces: `run_alpha_filter_from_files(...) -> dict[str, Any]`
- Produces: `freeze_filtered_pool(proposal_path, decisions_path, output_dir) -> dict[str, Any]`
- Produces CLI commands: `run-alpha-filter` and `freeze-filtered-pool`.
- Produces an Active Pool accepted by `daily_selection.validate_active_pool`.

- [ ] **Step 1: Write failing end-to-end fixture tests**

```python
def test_filter_run_accounts_for_every_catalog_row(tmp_path):
    result = run_alpha_filter_from_files(**_fixture_paths(tmp_path))
    diagnostics = pd.read_csv(result["diagnostics"])
    assert len(diagnostics) == 8
    assert diagnostics[["factor_id", "factor_version"]].duplicated().sum() == 0
    assert result["live_orders_submitted"] == 0


def test_filter_run_refuses_nonempty_output_directory(tmp_path):
    paths = _fixture_paths(tmp_path)
    paths["output"].mkdir()
    (paths["output"] / "existing.txt").write_text("keep")
    with pytest.raises(FileExistsError, match="OUTPUT_DIRECTORY_NOT_EMPTY"):
        run_alpha_filter_from_files(**paths)


def test_frozen_filtered_pool_is_accepted_by_daily_selection(tmp_path):
    proposal, decisions = _reviewed_five_factor_proposal(tmp_path)
    pool = freeze_filtered_pool(proposal, decisions, tmp_path / "frozen")
    assert len(validate_active_pool(pool, pool["provider"])) == 5
    assert pool["selection_evidence"] == "DEVELOPMENT_EXPOSED"
```

- [ ] **Step 2: Verify the tests fail**

Run: `PYTHONPATH=src .venv/bin/pytest -q tests/test_alpha_filter_workflow.py`

Expected: new workflow functions are unavailable.

- [ ] **Step 3: Implement streamed factor evaluation and immutable artifacts**

Evaluate one factor at a time through the existing safe evaluator, write its
weekly rank panel to a content-addressed Parquet cache, then release the full
daily panel before moving to the next factor. Write the required config,
manifest, diagnostics, yearly IC, conflict, decision, proposal, composite,
baseline and blocked-item artifacts into a new run directory.

- [ ] **Step 4: Implement proposal review and freeze**

Accept a decisions CSV containing `factor_id`, `factor_version`, `decision` and
`comment`. Require every selected row to come from the proposal and pass all
hard gates. Emit a non-overwritable pool bundle with equal weights, hashes,
evidence fields and the existing `selected_factors` compatibility structure.

- [ ] **Step 5: Update daily pool validation**

Change only the size contract from 6-8 to 5-12. Preserve hash, provider,
direction, uniqueness and equal-weight checks. Add a regression proving old
6-8 factor pools still validate.

- [ ] **Step 6: Add CLI commands**

`run-alpha-filter` accepts unified catalog, library, universe, panel directory,
provider, policy and output paths. `freeze-filtered-pool` accepts proposal,
decisions and output paths. Both emit `live_orders_submitted=0`; data blockers
return exit code 4 and configuration blockers return exit code 2.

- [ ] **Step 7: Run focused tests**

Run: `PYTHONPATH=src .venv/bin/pytest -q tests/test_alpha_filter_workflow.py tests/test_daily_selection.py tests/test_v5_lite_workflow.py`

Expected: all tests pass.

- [ ] **Step 8: Commit**

```bash
git add src/us_equity_alpha/alpha_filter_workflow.py src/us_equity_alpha/daily_selection.py src/us_equity_alpha/cli.py tests/test_alpha_filter_workflow.py tests/test_daily_selection.py
git commit -m "Run and freeze V5.1 lightweight alpha filter"
```

---

### Task 6: Flat Composite and Frozen Baseline Comparison

**Files:**
- Modify: `src/us_equity_alpha/alpha_filter_workflow.py`
- Create: `tests/test_alpha_filter_composite.py`

**Interfaces:**
- Produces: `flat_equal_weight_composite(rank_panels: Mapping[str, pd.DataFrame]) -> pd.DataFrame`
- Produces: `compare_composites(new_scores, baseline_scores, labels, portfolio_runner, metadata) -> pd.DataFrame`
- Consumes frozen `BASELINE_V5_8_FACTOR` IDs and the identical provider/universe/date/label contract.

- [ ] **Step 1: Write failing composite tests**

```python
def test_flat_composite_uses_equal_weights_and_complete_rows_only():
    result = flat_equal_weight_composite(_three_rank_panels_with_one_gap())
    assert result.loc["2026-01-02", "A"] == pytest.approx((0.2 + 0.4 + 0.6) / 3)
    assert pd.isna(result.loc["2026-01-02", "B"])


def test_comparison_rejects_mismatched_contracts():
    with pytest.raises(ValueError, match="BASELINE_CONTRACT_MISMATCH"):
        compare_composites(
            _new(), _baseline(), _labels(), _runner(),
            _metadata() | {"new_universe_hash": "a", "baseline_universe_hash": "b"},
        )


def test_portfolio_return_never_changes_factor_decisions():
    before = _filter_decisions().copy()
    compare_composites(_new(), _baseline(), _labels(), _runner(), _metadata())
    pd.testing.assert_frame_equal(before, _filter_decisions())
```

- [ ] **Step 2: Verify the tests fail**

Run: `PYTHONPATH=src .venv/bin/pytest -q tests/test_alpha_filter_composite.py`

Expected: new composite functions are unavailable.

- [ ] **Step 3: Implement flat composite and diagnostic comparison**

Average complete cross-sectional percentile ranks without fitted weights. Use
the existing Alphalens/vectorbt adapters only after filter decisions have been
frozen in memory. Emit Mean Rank IC, ICIR, positive-year ratio, Q5-Q1, weekly
turnover, total return, Sharpe, MDD and modeled fees for old and new composites.

- [ ] **Step 4: Run focused tests**

Run: `PYTHONPATH=src .venv/bin/pytest -q tests/test_alpha_filter_composite.py tests/test_backtest.py tests/test_v4_workflow_sample.py`

Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/us_equity_alpha/alpha_filter_workflow.py tests/test_alpha_filter_composite.py
git commit -m "Compare flat alpha composite with frozen baseline"
```

---

### Task 7: Human Review Workbook, Documentation, and Release Verification

**Files:**
- Create: `scripts/build_v5_1_alpha_filter_workbook.mjs`
- Create: `tests/test_v5_1_alpha_filter_report.py`
- Create: `docs/implementation/2026-09-16-v5-1-library-first-usage.md`
- Modify: `docs/superpowers/specs/2026-09-16-v5-1-lite-library-first-design.md` only if implementation exposes a concrete inconsistency; otherwise leave the approved spec unchanged.

**Interfaces:**
- Consumes one completed immutable filter-run directory.
- Produces `alpha_filter_review.xlsx` with exactly five sheets: `Summary`, `Alpha Diagnostics`, `Selected Pool`, `Composite Comparison`, `Audit`.

- [ ] **Step 1: Write failing report-contract test**

```python
def test_report_contains_only_required_review_sheets(tmp_path):
    run_dir = _completed_filter_run_fixture(tmp_path)
    output = tmp_path / "alpha_filter_review.xlsx"
    subprocess.run(
        ["node", "scripts/build_v5_1_alpha_filter_workbook.mjs", str(run_dir), str(output)],
        check=True,
    )
    workbook = openpyxl.load_workbook(output, data_only=False)
    assert workbook.sheetnames == [
        "Summary", "Alpha Diagnostics", "Selected Pool",
        "Composite Comparison", "Audit",
    ]
    audit = workbook["Audit"]
    text = " ".join(str(cell.value) for row in audit.iter_rows() for cell in row if cell.value)
    for required in [
        "DEVELOPMENT_EXPOSED", "historical_pit_verified=false",
        "t close -> t+1 open -> t+6 open", "live_orders_submitted=0",
    ]:
        assert required in text
```

- [ ] **Step 2: Verify the report test fails**

Run: `PYTHONPATH=src .venv/bin/pytest -q tests/test_v5_1_alpha_filter_report.py`

Expected: workbook builder fixture cannot find the new script/output.

- [ ] **Step 3: Build the compact review workbook**

Use `@oai/artifact-tool` with the repository's existing spreadsheet build
pattern. The workbook shows counts through each gate, all factor statuses,
selected factors, old/new composite metrics and the full evidence boundary.
Render every sheet in the test fixture and scan the exported workbook for
formula errors.

- [ ] **Step 4: Add a focused library-first usage guide**

Document registration, unified-catalog build, on-demand filter, human review,
pool freeze and unchanged daily-selection commands. State that filter runs are
not daily, historical results remain development-exposed and private data stay
ignored. Preserve the existing README unchanged, as previously requested.

- [ ] **Step 5: Run complete regression and safety checks**

Run:

```bash
PYTHONPATH=src .venv/bin/pytest -q
git diff --check
git grep -En '(TIINGO(_API)?_TOKEN|APCA_API_KEY_ID|APCA_API_SECRET_KEY|WQ_BRAIN_(EMAIL|PASSWORD))[[:space:]]*=[[:space:]]*[A-Za-z0-9]' -- . ':!dependency-lock.txt'
```

Expected: all tests pass, `git diff --check` is silent, and the credential
assignment scan has zero matches.

- [ ] **Step 6: Run one saved-data acceptance sample**

Use the existing saved 500-security development dataset. Confirm every V4
factor is accounted for, the run returns either a 5-12 factor proposal or the
specified composite blocker, the old eight-factor baseline remains present,
all manifests reopen with matching hashes and `live_orders_submitted=0`.

- [ ] **Step 7: Commit**

```bash
git add scripts/build_v5_1_alpha_filter_workbook.mjs tests/test_v5_1_alpha_filter_report.py docs/implementation/2026-09-16-v5-1-library-first-usage.md
git commit -m "Deliver V5.1 library-first alpha filter"
```

---

## Completion Gate

Before reporting completion:

1. Run the full test suite and record the exact pass/fail/warning counts.
2. Verify the V4 library and manifest hashes are unchanged.
3. Verify every generated manifest and frozen pool hash by reopening it.
4. Verify the saved-data sample is still labeled `DEVELOPMENT_EXPOSED` and
   survivorship-biased.
5. Verify no tracked file contains credentials or private raw data.
6. Review the final Git diff and list any design requirement that remains
   partial or blocked.
