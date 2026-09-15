# v1.4 local reconstruction execution ledger
Plan: ../2026-09-06-us-equity-alpha-implementation-plan.md, v1.4 contract.
- Workspace: existing implementation/phase1; continue in the user's project to reuse private evidence. Prior Tiingo edits preserved.
- Scope: new performance-blind reconstruction path; old migration artifacts and downstream gates unchanged.
- Task 1: complete — economic component extraction, deduplication, audit cards, tests.
- Task 2: complete — immutable library CLI, evidence replay, T3 adapter checks.
- Task 3: complete — full 886-source run, semantic/code review, report.
Ruling: all sources receive cards; data-insufficient or unknown mechanisms remain deferred. Never fabricate fundamental/analyst/option observables from OHLCV to achieve 886 formulas.
Ruling: use independent local risk settings, while labeling lost source group relations and preserving downstream industry constraints.

Verification 2026-09-09: 197 tests passed, 503 existing warnings. Independent scoped reviewer approved after three fixes. Final library reconstruction-v2: 886 cards, 246 reconstructed source associations, 96 unique formulas, 84 real sample compute verified (191 sources), 12 insufficient sample (55 sources), 640 deferred; research/released 0. Ordinary-bar T3 replay: Alpaca 84 / Tiingo 70; 164 manifest hashes verified. Prior v1 superseded, preserved as evidence.
Ruling: keep implementation/phase1 in the user's current project; no merge or remote publication requested.
