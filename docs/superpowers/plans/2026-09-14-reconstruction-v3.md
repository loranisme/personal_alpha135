# Reconstruction V3 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Exhaust defensible OHLCV/SPY reconstruction opportunities in the 886-source registry and publish an immutable, audit-ready `reconstruction-v3` diagnostic library.

**Architecture:** Extend the capability catalog with reviewed derived market observables, correct conservative AST translation and component extraction, and overlay long-history verification without weakening semantic or release gates. Re-run the existing immutable pipeline into a new versioned directory.

**Tech Stack:** Python, pandas, NumPy, AST, Parquet/JSON/CSV, pytest

**Spec:** `docs/superpowers/specs/2026-09-14-reconstruction-v3-design.md`

## Global Constraints

- Never read platform performance during conversion.
- Never overwrite `reconstruction-v2`.
- All new factors remain `DIAGNOSTIC_ONLY`; research and release counts remain zero.
- Keep missing observations missing and record every semantic loss.
- Preserve all 886 source decisions and deterministic deduplication.

---

### Task 1: Derived local market capabilities

**Files:**
- Create: `src/us_equity_alpha/derived_capabilities.py`
- Modify: `src/us_equity_alpha/reconstruction_pipeline.py`
- Test: `tests/test_derived_capabilities.py`

**Interfaces:**
- Produces: `add_local_market_capabilities(inputs, capabilities) -> (inputs, capabilities)`.
- Consumes: provider panels containing verified OHLCV and optional `SPY` close.

- [x] Write failing tests for ATR, volume, correlation, beta, systematic and residual-risk bindings and provider gating.
- [x] Run `pytest -q tests/test_derived_capabilities.py` and confirm failures are caused by the missing module/API.
- [x] Implement reviewed capability definitions with explicit semantic-loss metadata.
- [x] Run the focused tests and existing reconstruction tests.

### Task 2: Conservative converter corrections

**Files:**
- Modify: `src/us_equity_alpha/reconstruction.py`
- Test: `tests/test_reconstruction_v3.py`

**Interfaces:**
- Consumes: authorized capability records from Task 1.
- Produces: local candidates and source lineages with precise semantic states/losses.

- [x] Write failing tests for translated-AST substantiveness, variadic filtered add, safe wrapper component extraction, conflict dual tracks and unverified regression deferral.
- [x] Run focused tests and verify the expected failures.
- [x] Implement the minimum AST and lineage changes.
- [x] Run focused and existing reconstruction tests.

### Task 3: Long-history verification overlay

**Files:**
- Create: `src/us_equity_alpha/history_verification.py`
- Modify: `src/us_equity_alpha/reconstruction_pipeline.py`
- Modify: `src/us_equity_alpha/cli.py`
- Test: `tests/test_history_verification.py`

**Interfaces:**
- Produces: `load_tiingo_history_snapshot(path, limit, benchmark_json=None)` and optional `--tiingo-history`/`--tiingo-benchmark-json` CLI inputs.
- Consumes: `universe.csv`, hashed `raw/<symbol>.json` files and optional SPY JSON.

- [x] Write failing tests for hash enforcement, aligned panels, optional benchmark, pass preservation and compiled promotion.
- [x] Run focused tests and verify expected failures.
- [x] Implement the loader and verification overlay.
- [x] Run focused and full reconstruction tests.

### Task 4: V3 immutable build and audit report

**Files:**
- Create: `private/project_factor_library/reconstruction-v3/**` through the pipeline.
- Create: `docs/implementation/2026-09-14-reconstruction-v3-progress.md`
- Modify: `../2026-09-06-us-equity-alpha-implementation-plan.md`

**Interfaces:**
- Consumes: frozen registry, field mapping, two existing provider bundles and the long-history Tiingo cache.
- Produces: cards, decisions, formulas, matrices, source index, conversion report and manifest.

- [x] Run the V3 pipeline into a new empty directory.
- [x] Compare V2/V3 source coverage, semantic states, formula deduplication and compute states.
- [x] Verify all manifest hashes, absence of credential-like strings and V2 immutability.
- [x] Run the full test suite and document observed results and remaining blockers (`321 passed`, 2026-09-14).
