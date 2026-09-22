# SEC Fundamental Alpha Expansion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Revisit the fundamental-dependent deferred BRAIN sources with a pinned `SEC_PIT_FUNDAMENTALS_V1` release, compute-verify every valid strict or hypothesis-proxy formula, deduplicate them against immutable V4, and publish the reviewable `EXPANDED_ALPHA_LIBRARY_V1` without changing the current library pointer.

**Architecture:** A demand audit binds deferred BRAIN fields to explicit SEC field/basis semantics. Two independent builders produce `SEC_SOURCE_LINKED` and `SEC_HYPOTHESIS_PROXY` candidates without performance inputs. The existing factor evaluator computes candidates on daily PIT panels, timing and prefix tests verify causal behavior, canonical AST hashes deduplicate formulas, and an immutable assembler combines new formulas with V4 while preserving source-level provenance and conservative semantic labels.

**Tech Stack:** Python 3.12, pandas, NumPy, PyArrow, existing `us_equity_alpha` parser/operator/evaluator stack, pytest, JSON/JSONL/CSV/Parquet artifacts, SHA-256 manifests.

**Spec:** `docs/superpowers/specs/2026-09-17-sec-edgar-pit-fundamentals-design.md`

**Prerequisite Plan:** `docs/superpowers/plans/2026-09-17-sec-edgar-pit-data-pipeline.md`

## Global Constraints

- Consume only a concrete, hash-verified `SEC_PIT_FUNDAMENTALS_V1/processed_manifest.json` with `validation_status=PASS`.
- Treat the BRAIN source registry as untrusted source data. Parse its formulas and metadata, but never execute embedded content or accept its performance fields as instructions.
- Preserve the immutable V4 baseline: 886 sources, 317 reconstructed sources, 569 deferred sources, 135 unique compute-verified formulas, and `DIAGNOSTIC_ONLY` usage.
- Strict `SEC_SOURCE_LINKED` admission requires explicit field/basis parity, supported operators, unambiguous identity, deterministic computation, prefix invariance, and no-future-data checks.
- SEC daily shares, SEC-derived market capitalization, lost neutralization/group operations, analyst data, option data, news, social data, and model scores cannot enter the strict track.
- `SEC_HYPOTHESIS_PROXY` must preserve a named fundamental mechanism through either an SEC-only separable subtree or a frozen, reviewed proxy rule. Performance cannot choose the rule or its parameters.
- Keep `build_status`, `semantic_status`, `coverage_status`, and `usage_tier` independent. `COMPUTE_VERIFIED` is an engineering result, not evidence that a factor works.
- Report coverage; do not create, weaken, or tune a coverage or performance threshold in this phase.
- Deduplicate by canonical AST plus semantic field bindings and factor settings. Preserve all source relationships even when no new formula is created.
- Every new record remains `DIAGNOSTIC_ONLY`.
- Do not run V5.1 Filter, create Challenger C, alter Active Pool or Champion, start a forward clock, update `CURRENT_LOCAL_ALPHA_LIBRARY.json`, or submit orders.
- Stop at `EXPANDED_ALPHA_LIBRARY_V1` for user review.

---

### Task 1: Build the Fundamental Deferred-Source Demand Matrix

**Files:**
- Create: `src/us_equity_alpha/sec_field_mapping.py`
- Create: `src/us_equity_alpha/sec_deferred_audit.py`
- Create: `config/sec_brain_field_bindings.json`
- Create: `tests/test_sec_field_mapping.py`
- Create: `tests/test_sec_deferred_audit.py`

**Interfaces:**

```text
@dataclass(frozen=True)
class SecFieldBinding:
    brain_field: str
    canonical_field: str
    required_basis: str
    semantic_grade: str
    direction_or_sign_rule: str
    allowed_track: str
    mapping_evidence: str


def load_sec_field_bindings(path: Path | str, sec_field_catalog: pd.DataFrame) -> dict[str, SecFieldBinding]
def audit_fundamental_deferred_sources(registry: Mapping[str, Any], v4_source_index: pd.DataFrame, brain_field_catalog: Sequence[Mapping[str, Any]], bindings: Mapping[str, SecFieldBinding]) -> pd.DataFrame
```

- [ ] **Step 1: Write failing binding tests**

```python
def test_binding_requires_field_basis_and_semantic_grade(tmp_path):
    path = tmp_path / "bindings.json"
    path.write_text(json.dumps([{
        "brain_field": "assets",
        "canonical_field": "total_assets",
        "required_basis": "INSTANT",
        "semantic_grade": "SOURCE_LINKED_DIRECT",
        "direction_or_sign_rule": "AS_REPORTED",
        "allowed_track": "SEC_SOURCE_LINKED",
        "mapping_evidence": "BRAIN assets matched to reviewed SEC Assets concept"
    }]))
    bindings = load_sec_field_bindings(path, pd.DataFrame([{
        "canonical_field": "total_assets", "supported_basis": "INSTANT"
    }]))
    assert bindings["assets"].required_basis == "INSTANT"


def test_shares_and_cap_are_proxy_only(tmp_path, sec_field_catalog):
    path = tmp_path / "bindings.json"
    path.write_text(json.dumps([{
        "brain_field": "cap",
        "canonical_field": "market_cap_sec_proxy",
        "required_basis": "INSTANT",
        "semantic_grade": "HYPOTHESIS_PROXY",
        "direction_or_sign_rule": "POSITIVE",
        "allowed_track": "SEC_HYPOTHESIS_PROXY",
        "mapping_evidence": "SEC shares times visible raw close"
    }]))
    assert load_sec_field_bindings(path, sec_field_catalog)["cap"].allowed_track == "SEC_HYPOTHESIS_PROXY"
```

Reject unknown SEC fields, unsupported bases, duplicate BRAIN-field keys, undocumented sign changes, and any `cap` or `sharesout` row marked strict.

- [ ] **Step 2: Write failing demand-audit tests**

```python
def test_audit_classifies_all_deferred_sources_once(registry_fixture, v4_index_fixture, field_catalog_fixture, bindings_fixture):
    result = audit_fundamental_deferred_sources(registry_fixture, v4_index_fixture, field_catalog_fixture, bindings_fixture)
    assert len(result) == 569
    assert result.source_alpha_id.nunique() == 569
    assert set(result.demand_class) == {"FUNDAMENTAL_ONLY", "MIXED_DOMAIN", "NO_FUNDAMENTAL"}


def test_audit_never_uses_brain_performance(registry_fixture, v4_index_fixture, field_catalog_fixture, bindings_fixture):
    changed = copy.deepcopy(registry_fixture)
    for record in changed["records"]:
        record["raw"]["is"] = {"sharpe": -999.0, "fitness": 999.0}
    left = audit_fundamental_deferred_sources(registry_fixture, v4_index_fixture, field_catalog_fixture, bindings_fixture)
    right = audit_fundamental_deferred_sources(changed, v4_index_fixture, field_catalog_fixture, bindings_fixture)
    pd.testing.assert_frame_equal(left, right)
```

- [ ] **Step 3: Verify tests fail**

Run:

```bash
PYTHONPATH=src .venv/bin/pytest -q tests/test_sec_field_mapping.py tests/test_sec_deferred_audit.py
```

Expected: imports fail for both new modules.

- [ ] **Step 4: Freeze reviewed BRAIN-to-SEC bindings**

Create one versioned JSON record per supported BRAIN fundamental field. Bind explicit accounting meaning and period basis; examples include `assets -> total_assets/INSTANT`, `liabilities -> total_liabilities/INSTANT`, `equity -> stockholders_equity/INSTANT`, `sales -> revenue/TTM`, `operating_income -> operating_income/TTM`, `net_income -> net_income/TTM`, `cashflow_op -> operating_cash_flow/TTM`, `capex -> capital_expenditure/TTM`, `debt -> total_debt/INSTANT`, `cash -> cash_and_cash_equivalents/INSTANT`, `eps -> diluted_eps/TTM`, and `bookvalue_ps -> book_value_per_share/INSTANT`. A field without reviewed meaning stays absent from the binding file and remains deferred.

The EPS binding is present only when the pinned SEC release explicitly advertises the `diluted_eps/TTM` series; it never falls back to annual or latest-quarter EPS.

- [ ] **Step 5: Implement complete source classification**

Join the 886 source records to the V4 source index by `source_alpha_id`, then retain the 569 rows whose V4 decision is deferred. Derive field domains only from the saved BRAIN field catalog. Emit `source_alpha_id`, `original_expression_hash`, `missing_fields`, `fundamental_fields`, `other_domain_fields`, `mapped_fields`, `unmapped_fundamental_fields`, `required_bindings`, `demand_class`, `candidate_track`, and `blocking_reasons`. Assert the audit totals 86 fundamental-only, 203 mixed-domain, and 280 no-fundamental rows for the frozen inputs; a changed count is an input-version mismatch, not a silently accepted baseline.

- [ ] **Step 6: Run focused tests**

Run:

```bash
PYTHONPATH=src .venv/bin/pytest -q tests/test_sec_field_mapping.py tests/test_sec_deferred_audit.py
```

Expected: all tests pass.

- [ ] **Step 7: Commit**

```bash
git add config/sec_brain_field_bindings.json src/us_equity_alpha/sec_field_mapping.py src/us_equity_alpha/sec_deferred_audit.py tests/test_sec_field_mapping.py tests/test_sec_deferred_audit.py
git commit -m "Audit deferred fundamental source demand"
```

---

### Task 2: Bind SEC Panels to the Existing Factor Engine

**Files:**
- Create: `src/us_equity_alpha/sec_factor_inputs.py`
- Modify: `src/us_equity_alpha/factor_library.py`
- Create: `tests/test_sec_factor_inputs.py`
- Modify: `tests/test_factor_library.py`

**Interfaces:**

```text
def binding_alias(binding: SecFieldBinding) -> str
def load_sec_factor_inputs(provider: SecFundamentalProvider, bindings: Sequence[SecFieldBinding], start: str, end: str, mode: str = "HISTORICAL_DIAGNOSTIC") -> tuple[dict[str, pd.DataFrame], dict[str, Any]]
def compute_sec_registry_factors(library: Mapping[str, Any], factor_ids: Sequence[str], inputs: Mapping[str, pd.DataFrame], input_evidence: Mapping[str, Any], usage: str = "diagnostic") -> dict[str, pd.DataFrame]
```

- [ ] **Step 1: Write failing input-binding tests**

```python
def test_alias_encodes_field_and_basis():
    binding = SecFieldBinding("sales", "revenue", "TTM", "SOURCE_LINKED_DIRECT", "AS_REPORTED", "SEC_SOURCE_LINKED", "reviewed")
    assert binding_alias(binding) == "sec__revenue__TTM"


def test_input_loader_preserves_daily_sessions_and_provider_hashes(sec_provider_fixture):
    bindings = [SecFieldBinding("sales", "revenue", "TTM", "SOURCE_LINKED_DIRECT", "AS_REPORTED", "SEC_SOURCE_LINKED", "reviewed")]
    panels, evidence = load_sec_factor_inputs(sec_provider_fixture, bindings, "2020-01-02", "2020-03-31")
    assert set(panels) == {"sec__revenue__TTM"}
    assert panels["sec__revenue__TTM"].index.freq is None
    assert evidence["dataset_version"] == "SEC_PIT_FUNDAMENTALS_V1"
    assert len(evidence["processed_manifest_sha256"]) == 64
```

- [ ] **Step 2: Write failing factor-library gate tests**

```python
def test_sec_factor_compute_requires_hash_bound_input_evidence(sec_factor_library, sec_inputs):
    with pytest.raises(ValueError, match="SEC_INPUT_EVIDENCE_MISSING"):
        compute_sec_registry_factors(sec_factor_library, ["sec_factor_a"], sec_inputs, {})


def test_sec_factor_compute_rejects_unrequested_field(sec_factor_library, sec_inputs, sec_input_evidence):
    sec_inputs["sec__future_field__TTM"] = sec_inputs["sec__revenue__TTM"]
    with pytest.raises(ValueError, match="SEC_INPUT_SET_MISMATCH"):
        compute_sec_registry_factors(sec_factor_library, ["sec_factor_a"], sec_inputs, sec_input_evidence)
```

- [ ] **Step 3: Verify tests fail**

Run:

```bash
PYTHONPATH=src .venv/bin/pytest -q tests/test_sec_factor_inputs.py tests/test_factor_library.py
```

Expected: import or attribute failures for the new interfaces.

- [ ] **Step 4: Implement exact alias and evidence binding**

Load only requested field/basis pairs from `SecFundamentalProvider`. Require matching dataset version, manifest hash, mapping hash, derived-definition hash, identity hash, PIT-contract hash, availability mode, start/end dates, and security IDs. Return daily panels without weekly resampling, backward fill, zero fill, or basis substitution.

- [ ] **Step 5: Extend the factor adapter without weakening existing paths**

Add `compute_sec_registry_factors` as a separate entry point. Require `provenance_kind` in `{"SEC_SOURCE_LINKED", "SEC_HYPOTHESIS_PROXY"}`, `usage_tier=DIAGNOSTIC_ONLY`, declared input aliases exactly matching provided panels, and a verified SEC evidence block. Continue using the existing `evaluate_factor` implementation and operators. Do not change `compute_registry_factors` behavior for V4.

- [ ] **Step 6: Run focused regression tests**

Run:

```bash
PYTHONPATH=src .venv/bin/pytest -q tests/test_sec_factor_inputs.py tests/test_factor_library.py tests/test_v4_workflow_sample.py
```

Expected: all tests pass and V4 computation is unchanged.

- [ ] **Step 7: Commit**

```bash
git add src/us_equity_alpha/sec_factor_inputs.py src/us_equity_alpha/factor_library.py tests/test_sec_factor_inputs.py tests/test_factor_library.py
git commit -m "Bind SEC PIT panels to factor computation"
```

---

### Task 3: Build the Strict `SEC_SOURCE_LINKED` Track

**Files:**
- Create: `src/us_equity_alpha/sec_source_reconstruction.py`
- Create: `tests/test_sec_source_reconstruction.py`

**Interfaces:**

```text
def reconstruct_sec_source_linked(source: Mapping[str, Any], demand_row: Mapping[str, Any], bindings: Mapping[str, SecFieldBinding], supported_operators: set[str]) -> dict[str, Any]
def build_sec_source_linked_candidates(registry: Mapping[str, Any], demand: pd.DataFrame, bindings: Mapping[str, SecFieldBinding], supported_operators: set[str]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]
```

- [ ] **Step 1: Write failing strict-admission tests**

```python
def test_fundamental_only_source_with_complete_bindings_is_strict(source_assets_over_equity, complete_demand_row, strict_bindings):
    result = reconstruct_sec_source_linked(source_assets_over_equity, complete_demand_row, strict_bindings, {"divide", "rank"})
    assert result["semantic_status"] == "SOURCE_LINKED"
    assert result["provenance_kind"] == "SEC_SOURCE_LINKED"
    assert result["expression"] == "rank(sec__total_assets__INSTANT / sec__stockholders_equity__INSTANT)"
    assert result["usage_tier"] == "DIAGNOSTIC_ONLY"


def test_daily_cap_binding_blocks_strict_track(source_using_cap, cap_demand_row, bindings_with_proxy_cap):
    result = reconstruct_sec_source_linked(source_using_cap, cap_demand_row, bindings_with_proxy_cap, {"divide", "rank"})
    assert result["build_status"] == "DEFERRED_DATA"
    assert "PROXY_FIELD_NOT_ALLOWED_IN_SOURCE_LINKED:cap" in result["blocking_reasons"]


def test_unsupported_group_operator_blocks_strict_track(source_with_group_rank, complete_demand_row, strict_bindings):
    result = reconstruct_sec_source_linked(source_with_group_rank, complete_demand_row, strict_bindings, {"rank"})
    assert "UNSUPPORTED_OPERATOR:group_rank" in result["blocking_reasons"]
```

Also test incomplete field mapping, ambiguous identity requirement, unsupported syntax, unknown identifier, basis mismatch, per-share ambiguity, and mixed nonfundamental dependencies.

- [ ] **Step 2: Verify tests fail**

Run:

```bash
PYTHONPATH=src .venv/bin/pytest -q tests/test_sec_source_reconstruction.py
```

Expected: import fails for `sec_source_reconstruction`.

- [ ] **Step 3: Implement AST-level field substitution**

Parse the original expression with the existing safe expression parser. Replace only field identifier nodes that have strict bindings. Preserve numeric constants, operator arguments, settings, and expression structure. Do not perform string replacement. Canonically serialize the AST and record the original and local expression hashes.

- [ ] **Step 4: Implement strict admission ledger**

A candidate is emitted only when every dependency is supported, every mapped field allows `SEC_SOURCE_LINKED`, every basis exists in the pinned SEC catalog, group/classification operations retain their original semantics, and the issuer/security requirement is compatible with the field. Every blocked source receives one ledger row with all blockers; never re-route it silently to proxy.

Each strict candidate contains `local_factor_id`, `source_alpha_ids`, `parent_source_alpha_ids`, `original_expression_hash`, `expression`, `expression_hash`, `settings`, `field_bindings`, `required_fields`, `economic_hypothesis`, `build_status=COMPILED`, `semantic_status=SOURCE_LINKED`, `coverage_status=NOT_MEASURED`, `usage_tier=DIAGNOSTIC_ONLY`, `provenance_kind=SEC_SOURCE_LINKED`, and SEC evidence hashes.

- [ ] **Step 5: Run focused tests**

Run:

```bash
PYTHONPATH=src .venv/bin/pytest -q tests/test_sec_source_reconstruction.py tests/test_reconstruction.py tests/test_operators.py
```

Expected: all tests pass.

- [ ] **Step 6: Commit**

```bash
git add src/us_equity_alpha/sec_source_reconstruction.py tests/test_sec_source_reconstruction.py
git commit -m "Build strict SEC source-linked factors"
```

---

### Task 4: Build the Performance-Blind `SEC_HYPOTHESIS_PROXY` Track

**Files:**
- Create: `config/sec_proxy_hypotheses.json`
- Create: `src/us_equity_alpha/sec_proxy_reconstruction.py`
- Create: `tests/test_sec_proxy_reconstruction.py`

**Interfaces:**

```text
def extract_sec_only_subtrees(expression: str, fundamental_fields: set[str], unavailable_fields: set[str]) -> list[str]
def load_proxy_hypotheses(path: Path | str, bindings: Mapping[str, SecFieldBinding]) -> dict[str, Mapping[str, Any]]
def reconstruct_sec_hypothesis_proxy(source: Mapping[str, Any], demand_row: Mapping[str, Any], bindings: Mapping[str, SecFieldBinding], proxy_rules: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]
```

- [ ] **Step 1: Write failing separability tests**

```python
def test_extracts_maximal_sec_only_component_from_weighted_mixed_formula():
    expression = "0.25 * rank(cashflow_op / cap) + 0.75 * rank(option_skew_30 - option_skew_180)"
    result = extract_sec_only_subtrees(expression, {"cashflow_op", "cap"}, {"option_skew_30", "option_skew_180"})
    assert result == ["rank(cashflow_op / cap)"]


def test_entangled_mixed_expression_requires_frozen_rule():
    source = source_record("rank(cashflow_op * option_skew_30)")
    demand = demand_row(fundamental_fields=["cashflow_op"], other_domain_fields=["option_skew_30"])
    result = reconstruct_sec_hypothesis_proxy(source, demand, proxy_bindings(), {})
    assert result["build_status"] == "DEFERRED_DATA"
    assert "MIXED_EXPRESSION_NOT_SEPARABLE_AND_NO_FROZEN_RULE" in result["blocking_reasons"]
```

- [ ] **Step 2: Write failing provenance and performance-isolation tests**

```python
def test_proxy_records_all_semantic_losses(separable_source, proxy_demand, proxy_bindings_fixture):
    result = reconstruct_sec_hypothesis_proxy(separable_source, proxy_demand, proxy_bindings_fixture, {})
    assert result["semantic_status"] == "HYPOTHESIS_PROXY"
    assert result["discarded_information_domains"] == ["OPTIONS"]
    assert "SEC_CAP_IS_NOT_BRAIN_DAILY_CAP" in result["semantic_losses"]
    assert result["usage_tier"] == "DIAGNOSTIC_ONLY"


def test_proxy_definition_is_invariant_to_performance_fields(separable_source, proxy_demand, proxy_bindings_fixture):
    changed = copy.deepcopy(separable_source)
    changed["raw"]["is"] = {"sharpe": 1000.0, "returns": -1.0}
    left = reconstruct_sec_hypothesis_proxy(separable_source, proxy_demand, proxy_bindings_fixture, {})
    right = reconstruct_sec_hypothesis_proxy(changed, proxy_demand, proxy_bindings_fixture, {})
    assert left["proxy_expression_hash"] == right["proxy_expression_hash"]
```

- [ ] **Step 3: Verify tests fail**

Run:

```bash
PYTHONPATH=src .venv/bin/pytest -q tests/test_sec_proxy_reconstruction.py
```

Expected: import fails for `sec_proxy_reconstruction`.

- [ ] **Step 4: Freeze proxy-rule schema and reviewed mechanism set**

Each rule in `sec_proxy_hypotheses.json` is keyed by original expression hash and contains `parent_source_alpha_ids`, `proxy_expression`, `retained_economic_hypothesis`, `required_bindings`, `discarded_information_domains`, `semantic_losses`, `review_author`, `reviewed_at`, and `rule_version`. Initial generic mechanism templates may cover accounting value, profitability, cash-flow yield, balance-sheet strength, accrual quality, investment growth, and operating efficiency, but a template becomes executable only through an explicit expression-hash rule. No wildcard source matching is permitted.

- [ ] **Step 5: Implement conservative proxy construction**

Select the unique maximal subtree whose leaves are all bound SEC fundamental fields and constants. If several incomparable maximal subtrees exist, preserve each as a separately identified component only when the source expression combines them additively and their coefficients are fixed in the source; otherwise defer for explicit review. Proxy-only bindings such as `cap` and `sharesout` are allowed only here and must add their semantic-loss labels. Never introduce price momentum, returns, volatility, analyst, options, news, social, or vendor-model inputs to replace missing information.

- [ ] **Step 6: Run focused tests**

Run:

```bash
PYTHONPATH=src .venv/bin/pytest -q tests/test_sec_proxy_reconstruction.py tests/test_proxy_converter.py tests/test_reconstruction.py
```

Expected: all tests pass.

- [ ] **Step 7: Commit**

```bash
git add config/sec_proxy_hypotheses.json src/us_equity_alpha/sec_proxy_reconstruction.py tests/test_sec_proxy_reconstruction.py
git commit -m "Build SEC hypothesis-proxy factors"
```

---

### Task 5: Compute-Verify Timing, Determinism, and Coverage

**Files:**
- Create: `src/us_equity_alpha/sec_factor_verification.py`
- Create: `tests/test_sec_factor_verification.py`

**Interfaces:**

```text
def verify_sec_factor(candidate: Mapping[str, Any], inputs: Mapping[str, pd.DataFrame], input_evidence: Mapping[str, Any]) -> tuple[dict[str, Any], pd.DataFrame | None]
def verify_sec_candidates(candidates: Sequence[Mapping[str, Any]], inputs: Mapping[str, pd.DataFrame], input_evidence: Mapping[str, Any]) -> tuple[list[dict[str, Any]], dict[str, pd.DataFrame], pd.DataFrame]
```

- [ ] **Step 1: Write failing compute and prefix tests**

```python
def test_compute_verified_requires_deterministic_replay(candidate, daily_inputs, evidence):
    record, values = verify_sec_factor(candidate, daily_inputs, evidence)
    assert record["build_status"] == "COMPUTE_VERIFIED"
    assert values is not None
    assert record["deterministic_replay"] == "PASS"


def test_prefix_invariance_detects_future_dependency(candidate, daily_inputs, evidence, monkeypatch):
    monkeypatch.setattr("us_equity_alpha.sec_factor_verification.evaluate_factor", future_leaking_evaluator)
    record, values = verify_sec_factor(candidate, daily_inputs, evidence)
    assert values is None
    assert record["build_status"] == "COMPUTE_FAILED"
    assert "PREFIX_INVARIANCE_FAILED" in record["verification_failures"]
```

- [ ] **Step 2: Write failing no-future and coverage tests**

```python
def test_future_fact_mutation_cannot_change_prior_scores(candidate, daily_inputs, evidence):
    changed = {name: frame.copy() for name, frame in daily_inputs.items()}
    changed["sec__revenue__TTM"].loc["2025-01-02":] *= 1000
    left, left_values = verify_sec_factor(candidate, daily_inputs, evidence)
    right, right_values = verify_sec_factor(candidate, changed, evidence)
    pd.testing.assert_frame_equal(left_values.loc[:"2024-12-31"], right_values.loc[:"2024-12-31"])


def test_zero_observation_factor_fails_without_becoming_coverage_threshold(candidate, all_missing_inputs, evidence):
    record, values = verify_sec_factor(candidate, all_missing_inputs, evidence)
    assert values is None
    assert record["build_status"] == "COMPUTE_FAILED"
    assert record["coverage_status"] == "LIMITED"
    assert "NO_VALID_CROSS_SECTIONAL_OBSERVATIONS" in record["verification_failures"]
```

- [ ] **Step 3: Verify tests fail**

Run:

```bash
PYTHONPATH=src .venv/bin/pytest -q tests/test_sec_factor_verification.py
```

Expected: import fails for `sec_factor_verification`.

- [ ] **Step 4: Implement engineering verification**

For each candidate, validate input aliases and hashes, compile with the existing safe evaluator, compute twice and compare exactly, recompute on multiple chronological prefixes, mutate inputs strictly after each prefix, and assert earlier scores are unchanged. Reject infinite output and a matrix with no session containing at least two finite cross-sectional values. Persist no performance label or forward return.

- [ ] **Step 5: Implement descriptive coverage report**

Record requested session count, sessions with at least two finite values, first and last valid sessions, security count, securities ever valid, daily finite-count minimum/median/maximum, and missing fractions by field and factor. Set `coverage_status=MEASURED` when every requested session has at least two values; otherwise set `LIMITED`. This label is descriptive and does not reject a factor that has nonzero valid observations.

- [ ] **Step 6: Run focused tests**

Run:

```bash
PYTHONPATH=src .venv/bin/pytest -q tests/test_sec_factor_verification.py tests/test_factor_library.py tests/test_operators.py
```

Expected: all tests pass.

- [ ] **Step 7: Commit**

```bash
git add src/us_equity_alpha/sec_factor_verification.py tests/test_sec_factor_verification.py
git commit -m "Verify SEC factor computation and timing"
```

---

### Task 6: Deduplicate and Assemble the Expanded Library

**Files:**
- Create: `src/us_equity_alpha/expanded_library.py`
- Create: `tests/test_expanded_library.py`

**Interfaces:**

```text
def canonical_factor_identity(expression: str, field_bindings: Sequence[Mapping[str, Any]], settings: Mapping[str, Any]) -> tuple[str, str]
def deduplicate_sec_candidates(v4_library: Mapping[str, Any], candidates: Sequence[Mapping[str, Any]]) -> tuple[list[dict[str, Any]], pd.DataFrame, list[dict[str, Any]]]
def assemble_expanded_library(v4_library: Mapping[str, Any], unique_new_factors: Sequence[Mapping[str, Any]], source_relationships: Sequence[Mapping[str, Any]]) -> Mapping[str, Any]
```

- [ ] **Step 1: Write failing canonical-identity tests**

```python
def test_equivalent_whitespace_and_parentheses_share_identity():
    left = canonical_factor_identity("rank((a / b))", bindings(), settings())
    right = canonical_factor_identity(" rank(a/b) ", bindings(), settings())
    assert left == right


def test_same_ast_with_different_basis_is_not_duplicate():
    left = canonical_factor_identity("rank(x)", [{"alias": "x", "field": "revenue", "basis": "TTM"}], settings())
    right = canonical_factor_identity("rank(x)", [{"alias": "x", "field": "revenue", "basis": "ANNUAL"}], settings())
    assert left != right
```

- [ ] **Step 2: Write failing deduplication and immutability tests**

```python
def test_duplicate_against_v4_keeps_v4_id_and_adds_relationship(v4_library, sec_candidate_matching_v4):
    before = canonical_json_hash(v4_library)
    unique, report, relationships = deduplicate_sec_candidates(v4_library, [sec_candidate_matching_v4])
    assert unique == []
    assert report.iloc[0].dedup_reason == "DUPLICATE_V4"
    assert relationships[0]["local_factor_id"] == sec_candidate_matching_v4["matching_v4_factor_id"]
    assert canonical_json_hash(v4_library) == before


def test_source_linked_and_proxy_same_formula_keep_two_semantic_relationships(v4_library, strict_candidate, proxy_candidate_same_identity):
    unique, report, relationships = deduplicate_sec_candidates(v4_library, [strict_candidate, proxy_candidate_same_identity])
    assert len(unique) == 1
    assert {row["semantic_status"] for row in relationships} == {"SOURCE_LINKED", "HYPOTHESIS_PROXY"}
```

- [ ] **Step 3: Verify tests fail**

Run:

```bash
PYTHONPATH=src .venv/bin/pytest -q tests/test_expanded_library.py
```

Expected: import fails for `expanded_library`.

- [ ] **Step 4: Implement canonical AST identity**

Parse and normalize operator names, numeric literals, associative argument ordering only for operators proven commutative, and redundant parentheses. Hash canonical AST, sorted semantic binding records, and canonical settings. Do not collapse different bases, semantic field meanings, delays, decays, neutralization settings, truncation, or NaN policy.

- [ ] **Step 5: Implement three-level deduplication**

Compare against V4's 135 immutable formula identities, then within source-linked candidates, then within proxy candidates and across both tracks. A V4 duplicate retains its V4 ID. A new duplicate cluster gets one deterministic ID derived from the identity hash. Preserve one source relationship per `(source_alpha_id, semantic_status, reconstruction_rule_hash)` and record `DUPLICATE_V4`, `DUPLICATE_NEW_SOURCE_LINKED`, `DUPLICATE_NEW_PROXY`, or `DUPLICATE_CROSS_TRACK`.

- [ ] **Step 6: Assemble without mutating V4**

Deep-copy V4 factor records byte-for-byte in semantic content and append only unique new factor records. Set library schema version separately from V4's schema version. Include `base_library_manifest_sha256`, `base_factor_count=135`, new unique count, final unique count, SEC dataset and mapping hashes, all source relationships, and `usage_tier=DIAGNOSTIC_ONLY`. Do not write a current pointer.

- [ ] **Step 7: Run focused tests**

Run:

```bash
PYTHONPATH=src .venv/bin/pytest -q tests/test_expanded_library.py tests/test_factor_library.py tests/test_expansion_registry.py
```

Expected: all tests pass.

- [ ] **Step 8: Commit**

```bash
git add src/us_equity_alpha/expanded_library.py tests/test_expanded_library.py
git commit -m "Deduplicate and assemble expanded alpha library"
```

---

### Task 7: Add the Immutable Expansion Workflow and CLI

**Files:**
- Create: `src/us_equity_alpha/sec_alpha_expansion_workflow.py`
- Modify: `src/us_equity_alpha/cli.py`
- Create: `scripts/run_sec_alpha_expansion.py`
- Create: `tests/test_sec_alpha_expansion_workflow.py`
- Modify: `tests/test_runtime_cli.py`

**Interfaces:**

```text
def run_sec_alpha_expansion(registry_path: Path | str, brain_field_catalog_path: Path | str, v4_library_path: Path | str, v4_manifest_path: Path | str, v4_source_index_path: Path | str, sec_manifest_path: Path | str, field_bindings_path: Path | str, proxy_rules_path: Path | str, output_dir: Path | str) -> Mapping[str, Any]
```

CLI command:

```text
us-equity-alpha expand-sec-fundamentals
  --registry PATH
  --brain-field-catalog PATH
  --v4-library PATH
  --v4-manifest PATH
  --v4-source-index PATH
  --sec-manifest PATH
  --field-bindings PATH
  --proxy-rules PATH
  --output PATH
```

- [ ] **Step 1: Write failing end-to-end fixture test**

```python
def test_expansion_workflow_writes_exact_delivery(frozen_expansion_inputs, tmp_path):
    summary = run_sec_alpha_expansion(**frozen_expansion_inputs, output_dir=tmp_path)
    assert set(path.name for path in tmp_path.iterdir()) == {
        "expanded_alpha_library.json",
        "fundamental_deferred_source_map.csv",
        "sec_source_linked_factors.json",
        "sec_hypothesis_proxy_factors.json",
        "source_reconstruction_index.csv",
        "deduplication_report.csv",
        "conversion_report.json",
        "validation_report.md",
        "manifest.json",
        "factor_values",
    }
    assert summary["base_factor_count"] == 135
    assert summary["usage_tier"] == "DIAGNOSTIC_ONLY"
    assert summary["filter_executed"] is False
    assert summary["current_library_pointer_updated"] is False
    assert summary["live_orders_submitted"] == 0
```

- [ ] **Step 2: Write failing manifest and boundary tests**

```python
def test_workflow_rejects_unapproved_sec_release(frozen_expansion_inputs, tmp_path):
    manifest = json.loads(Path(frozen_expansion_inputs["sec_manifest_path"]).read_text())
    manifest["validation_status"] = "FAIL"
    bad = tmp_path / "bad_manifest.json"
    bad.write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="SEC_RELEASE_NOT_APPROVED"):
        run_sec_alpha_expansion(**(frozen_expansion_inputs | {"sec_manifest_path": bad}), output_dir=tmp_path / "out")


def test_workflow_rejects_nonempty_output(frozen_expansion_inputs, tmp_path):
    output = tmp_path / "out"
    output.mkdir()
    (output / "existing").write_text("x")
    with pytest.raises(FileExistsError, match="OUTPUT_DIRECTORY_NOT_EMPTY"):
        run_sec_alpha_expansion(**frozen_expansion_inputs, output_dir=output)
```

- [ ] **Step 3: Verify tests fail**

Run:

```bash
PYTHONPATH=src .venv/bin/pytest -q tests/test_sec_alpha_expansion_workflow.py tests/test_runtime_cli.py
```

Expected: import or CLI-parser failures.

- [ ] **Step 4: Implement staged fail-closed workflow**

The workflow must verify all input hashes before reading source records, assert the V4 baseline counts, build the demand matrix, build strict candidates, build proxy candidates, load only required daily panels, verify candidates, deduplicate, and assemble. If any stage fails, write no final manifest. Temporary work stays under the output parent and is atomically renamed only after all final hashes verify.

- [ ] **Step 5: Write exact delivery artifacts**

`source_reconstruction_index.csv` contains all 886 source IDs and identifies prior V4, newly strict, newly proxy, duplicate relationship, or deferred status. `conversion_report.json` separately reports deferred sources examined, strict source candidates, strict sources unlocked, proxy sources represented, compiled candidates, compute-verified candidates, duplicates against V4, duplicates within expansion, new unique formulas, and final expanded unique-formula count. No minimum count is enforced.

`validation_report.md` states the SEC release identity, current-universe survivorship bias, development exposure, timing checks, coverage limitations, semantic losses, V4 immutability result, and the explicit non-execution boundaries.

- [ ] **Step 6: Implement CLI without implicit defaults**

Require every path shown in the interface. Resolve no `latest` symlink and do not read `CURRENT_LOCAL_ALPHA_LIBRARY.json`. Return exit code 2 for input or policy blockers and exit code 1 for an unexpected engineering failure. Print a JSON summary without source formulas, raw SEC payloads, credentials, or request headers.

- [ ] **Step 7: Run focused integration tests**

Run:

```bash
PYTHONPATH=src .venv/bin/pytest -q tests/test_sec_alpha_expansion_workflow.py tests/test_runtime_cli.py tests/test_reconstruction_pipeline.py
```

Expected: all tests pass and the existing reconstruction CLI remains unchanged.

- [ ] **Step 8: Commit**

```bash
git add src/us_equity_alpha/sec_alpha_expansion_workflow.py src/us_equity_alpha/cli.py scripts/run_sec_alpha_expansion.py tests/test_sec_alpha_expansion_workflow.py tests/test_runtime_cli.py
git commit -m "Add immutable SEC alpha expansion workflow"
```

---

### Task 8: Run Full Acceptance and Publish `EXPANDED_ALPHA_LIBRARY_V1`

**Files:**
- Create runtime output only: `private/project_factor_library/EXPANDED_ALPHA_LIBRARY_V1/`
- Verify only: `private/project_factor_library/CURRENT_LOCAL_ALPHA_LIBRARY.json`
- Verify only: `private/project_factor_library/reconstruction-v4/`

**Interfaces:**

```text
Consumes: run_sec_alpha_expansion with the exact pinned inputs from Task 7
Produces: private/project_factor_library/EXPANDED_ALPHA_LIBRARY_V1/manifest.json
Mutates: no existing library, pointer, filter, pool, forward, portfolio, or broker artifact
```

- [ ] **Step 1: Run the entire automated suite before private data**

Run:

```bash
PYTHONPATH=data_pipelines/sec_edgar/src:src .venv/bin/pytest -q data_pipelines/sec_edgar/tests tests/test_sec_fundamentals.py tests/test_sec_field_mapping.py tests/test_sec_deferred_audit.py tests/test_sec_factor_inputs.py tests/test_sec_source_reconstruction.py tests/test_sec_proxy_reconstruction.py tests/test_sec_factor_verification.py tests/test_expanded_library.py tests/test_sec_alpha_expansion_workflow.py
PYTHONPATH=src .venv/bin/pytest -q
git diff --check
```

Expected: all tests pass and `git diff --check` emits no output.

- [ ] **Step 2: Record immutable pre-run evidence**

Write a local acceptance ledger containing SHA-256 values for:

```text
private/project_factor_library/CURRENT_LOCAL_ALPHA_LIBRARY.json
private/project_factor_library/reconstruction-v4/manifest.json
private/project_factor_library/reconstruction-v4/project_factor_library.json
private/project_factor_library/reconstruction-v4/source_reconstruction_index.csv
private/sec_edgar/releases/SEC_PIT_FUNDAMENTALS_V1/processed_manifest.json
private/runs/brain-sync-20260907-live/alpha_export.json
private/local_snapshots/wq_usa_top3000_delay1_data_fields.json
config/sec_brain_field_bindings.json
config/sec_proxy_hypotheses.json
```

The ledger lives inside the new output directory after the atomic build and is included in `manifest.json` as `input_hashes`; it must not alter any input.

- [ ] **Step 3: Run the pinned expansion once**

Run:

```bash
PYTHONPATH=data_pipelines/sec_edgar/src:src .venv/bin/python scripts/run_sec_alpha_expansion.py --registry private/runs/brain-sync-20260907-live/alpha_export.json --brain-field-catalog private/local_snapshots/wq_usa_top3000_delay1_data_fields.json --v4-library private/project_factor_library/reconstruction-v4/project_factor_library.json --v4-manifest private/project_factor_library/reconstruction-v4/manifest.json --v4-source-index private/project_factor_library/reconstruction-v4/source_reconstruction_index.csv --sec-manifest private/sec_edgar/releases/SEC_PIT_FUNDAMENTALS_V1/processed_manifest.json --field-bindings config/sec_brain_field_bindings.json --proxy-rules config/sec_proxy_hypotheses.json --output private/project_factor_library/EXPANDED_ALPHA_LIBRARY_V1
```

Expected: the command completes only when all input and output hashes verify. A zero-new-factor outcome is allowed if the reports prove why every candidate was blocked or deduplicated.

- [ ] **Step 4: Verify counts and semantic partitions**

Run a read-only audit that asserts:

```text
BRAIN source records = 886
V4 reconstructed source records = 317
V4 deferred source records = 569
V4 base formulas = 135
all 569 deferred rows classified once
all new relationships are SOURCE_LINKED or HYPOTHESIS_PROXY
all new factor usage tiers are DIAGNOSTIC_ONLY
final unique count = 135 + new unique formula count
no source row disappears after deduplication
```

The report must retain the observed 86/203/280 demand split for the frozen input hashes or stop with `BASELINE_DEMAND_SPLIT_MISMATCH`.

- [ ] **Step 5: Verify immutability and non-execution boundaries**

Re-hash every pre-run input and compare to the recorded values. Assert the current pointer file is byte-identical, no V4 file changed, `filter_executed=false`, `challenger_created=false`, `forward_clock_started=false`, `current_library_pointer_updated=false`, and `live_orders_submitted=0`.

- [ ] **Step 6: Scan final artifacts for credentials and uncontrolled outputs**

Run:

```bash
rg -n -i '(api[_-]?key|secret|password|bearer|authorization|user-agent)' private/project_factor_library/EXPANDED_ALPHA_LIBRARY_V1
find private/project_factor_library/EXPANDED_ALPHA_LIBRARY_V1 -type l -print
```

Expected: the first command finds no credential material or request headers; the second finds no symlinks.

- [ ] **Step 7: Produce the review handoff**

Present the two manifests, validation status, exact count funnel, strict/proxy factor lists, blocked reasons, coverage report, deduplication report, V4 immutability hashes, and explicit limitations. Stop for user review. Do not update the current-library pointer or invoke the Alpha Filter.

- [ ] **Step 8: Commit implementation code and committed configuration only**

```bash
git add config/sec_brain_field_bindings.json config/sec_proxy_hypotheses.json src/us_equity_alpha scripts/run_sec_alpha_expansion.py tests
git commit -m "Deliver SEC fundamental alpha expansion pipeline"
```

Do not add `private/project_factor_library/EXPANDED_ALPHA_LIBRARY_V1` to Git.

---

## Plan-Level Acceptance

The plan is complete only when:

- `EXPANDED_ALPHA_LIBRARY_V1/manifest.json` verifies every delivered artifact and every pinned input;
- all 569 V4-deferred sources have exactly one new audit row, including the 280 sources outside SEC fundamental scope;
- every strict relationship proves explicit field/basis parity and no unsupported semantic substitution;
- every proxy relationship records retained economic hypothesis, discarded domains, semantic losses, and a frozen rule or separable source subtree;
- every included new formula is `COMPUTE_VERIFIED`, timing-safe, deterministically replayable, and `DIAGNOSTIC_ONLY`;
- limited coverage remains visible and has not been used to tune or relax a gate;
- V4's 135 factors and all V4 hashes remain unchanged;
- duplicates preserve source provenance without inflating unique factor count;
- the current library pointer remains unchanged;
- no V5.1 Filter, Challenger C, forward clock, portfolio construction, rebalance draft, or broker action has run.
