# US Equity Alpha — Phase 1

Lightweight migration pipeline for turning a read-only WorldQuant BRAIN Alpha
library into an evidence-bounded U.S. equity signal project. Phase 1 covers T0–T3:
contracts, BRAIN metadata synchronization, field/source mapping, point-in-time
data rules, and the minimal operator subset required by the source-supported MVP.
It does not submit Alphas or orders.

## Current evidence status (2026-09-07)

- BRAIN current-library sync: 886 records, 886 unique Alpha IDs, 9 pages,
  declared count reached, no duplicates.
- Search-history completeness: unknown. The API result is the current Alpha
  library, not proof that every historical experiment is retained.
- Dependencies: 202 distinct fields and 32 observed operators.
- Parser: 885/886 expressions have supported syntax. One expression using `^`
  remains blocked until its BRAIN semantics are confirmed.
- Field mapping: 34 direct candidates, 78 local proxies, 79 specialist-source
  fields, and 11 unresolved fields.
- Exact provider certification: zero. No historical BRAIN field values were
  available for numerical parity testing.
- Runnable direct-candidate MVP: five Alphas; all five execute against
  synthetic aligned matrices with the frozen six-operator subset. This is an
  engineering check, not a performance result or numerical parity claim.

## Data-source decision

Use Tiingo EOD and Tiingo Fundamentals as the Phase 1 market/fundamental data
candidates, with SEC EDGAR Company Facts for filing timing and revision
evidence. A separately frozen local classification is required for industry and
subindustry; its IDs and results are explicitly treated as proxies. Alpaca is
reserved for later quote/execution validation and optional stock VWAP or raw
option data. Neither provider, nor their combination, reproduces the complete
library by current evidence.

## Commands

Create the environment from `dependency-lock.txt`, then install the local wheel
without build isolation so pip uses the already locked build dependency:

```bash
.venv/bin/python -m pip install --no-build-isolation --no-deps .
```

Run the installed CLI directly:

```bash
.venv/bin/us-equity-alpha environment-check \
  --output runs/environment/environment_check.json

.venv/bin/us-equity-alpha login-brain \
  --session-file private/brain_session.json

.venv/bin/us-equity-alpha sync-brain \
  --session-file private/brain_session.json \
  --output private/runs/brain-sync

.venv/bin/us-equity-alpha map-library \
  --registry private/runs/t1-live-import/alpha_registry.json \
  --field-catalog private/local_snapshots/wq_usa_top3000_delay1_data_fields.json \
  --output private/runs/t2-live-mapping

PYTHONPATH=src .venv/bin/python -m pytest -q
```

Credentials, account data, Alpha IDs, formulas, raw pages, and generated
registries remain under ignored `private/` or `runs/` paths.
