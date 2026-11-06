# Alpha Performance Visual Output Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make each requested single-Alpha performance run produce a readable chart page from its actual diagnostic and backtest outputs.

**Architecture:** The run persists weekly decile forward-return series beside existing Alphalens and vectorbt outputs. A small renderer reads those files, writes a PNG and a short Markdown explanation, and is called only for that run.

**Tech Stack:** Python 3.12, pandas, matplotlib, Alphalens Reloaded, vectorbt.

**Spec:** `docs/superpowers/specs/2026-11-05-alpha-performance-visual-output-design.md`

## Global Constraints

- Do not change V4 factor definitions, V5.1 gates, current pointer or release status.
- Use the existing weekly signal and 5D forward label. Do not fabricate 1D/21D or cost scenarios.
- Current Yahoo sample is `DEVELOPMENT_EXPOSED`, not independent OOS.

## Review Focus

- DST changes: chart by exchange session labels, not fixed UTC close hours.
- Missing future returns: omit the week, do not fill forward returns.
- Tied factor scores: deterministic decile assignment and tie warning.
- Costs: report only simulated 10 bp series actually present.
- Unavailable group series: label report `PARTIAL`, do not draw a false curve.

### Task 1: Save per-run decile series

**Files:** Modify `outputs/single-factor-demo-20261105/run_demo.py` as a sample integration. Test in `tests/test_alpha_performance_visual.py`.

- [x] Write failing test for deterministic decile weekly return table and missing-label behavior.
- [x] Add pure function to `src/us_equity_alpha/performance_visual.py`.
- [x] Verify test passes, then call from the sample and save `alphalens/decile_5d_returns.csv`.

### Task 2: Render the actual output page

**Files:** Extend `src/us_equity_alpha/performance_visual.py`, modify sample runner and `pyproject.toml`, test in `tests/test_alpha_performance_visual.py`.

- [x] Write failing test that a report from actual CSV/JSON inputs creates PNG/MD and labels available horizons and costs honestly.
- [x] Render two date panels for deciles, 5D RankIC, and portfolio NAV/benchmarks.
- [x] Wire renderer into sample finalization and include files in manifest.
- [x] Run focused tests and sample end to end; inspect PNG visually; verify manifest hashes and original V4 hash.
