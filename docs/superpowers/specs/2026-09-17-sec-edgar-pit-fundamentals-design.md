# SEC EDGAR PIT Fundamentals and Expanded Alpha Library Design

**Status:** Approved design

**Date:** 2026-09-17

**Project:** Personal Alpha Library

**Delivery boundary:** `EXPANDED_ALPHA_LIBRARY_V1`

## 1. Purpose

Build an isolated SEC EDGAR point-in-time fundamentals pipeline that supplies
versioned canonical fields to the existing Alpha project, then use those fields
to revisit the fundamental-dependent WorldQuant BRAIN sources that are currently
deferred.

The work expands the personal Alpha library. It does not create a second Alpha
selection platform, change the approved V5.1 Lite filter, replace the existing
V4 library, or authorize trading.

## 2. Current baseline

The immutable starting point is the current reconstruction V4 library:

| Item | Count / status |
|---|---:|
| BRAIN source records | 886 |
| Reconstructed source records | 317 |
| Deferred source records | 569 |
| Unique local formulas | 135 |
| Compute-verified local formulas | 135 |
| Usage tier | `DIAGNOSTIC_ONLY` |

The deferred-source audit, using the saved BRAIN field-category metadata bound
to reconstruction V4, found:

| Deferred-source class | Count | Treatment |
|---|---:|---|
| Every missing field is categorized as Fundamental | 86 | Strict reconstruction candidates |
| Fundamental fields mixed with analyst, options, news, social, or model fields | 203 | Proxy candidates only when the fundamental component is separable or a related hypothesis is explicitly frozen |
| No Fundamental missing field | 280 | Outside this pipeline |

The figures above are source counts, not promised formula counts. Mapping,
coverage, operator support, compute verification, and deduplication can reduce
the final number of new unique formulas.

## 3. Approved decisions

1. Use two reconstruction tracks: `SEC_SOURCE_LINKED` and
   `SEC_HYPOTHESIS_PROXY`.
2. Acquire only the current 500-stock development universe by CIK. Do not
   implement an all-SEC bulk download in V1.
3. Support U.S. domestic US-GAAP issuers filing `10-K`, `10-Q`, `10-K/A`, and
   `10-Q/A`; retain ordinary shares and REITs within the project universe.
4. Defer ADRs, IFRS issuers, `20-F`, `40-F`, and ambiguous security-class
   mappings.
5. Use standard, entity-wide Company Facts only. Do not parse custom XBRL
   extensions or extract numeric facts from filing HTML in V1.
6. Set historical availability to `acceptance_datetime + 10 minutes`.
   Date-only records first become eligible at the next exchange-session signal
   cutoff. Forward use must also respect the actual `first_seen_at` time.
7. Preserve raw reported facts and expose explicit `INSTANT`,
   `QUARTERLY_DISCRETE`, `ANNUAL`, and `TTM` bases. Every BRAIN field mapping
   binds to one declared basis.
8. Treat SEC shares outstanding and market capitalization derived from those
   shares as proxy semantics, not strict parity with BRAIN daily `sharesout` or
   `cap`.
9. Replace the original minimal 13-field proposal with a demand-driven V1
   canonical field set.
10. Build the PIT factor input panel on every exchange session. Weekly sampling
    belongs to the later V5.1 evaluation layer and must not change daily
    time-series windows.
11. Stop this delivery at `EXPANDED_ALPHA_LIBRARY_V1`. V5.1 Filter execution,
    Challenger C, and a new forward clock require a later review.

## 4. Architecture and isolation boundary

Code and configuration live in the existing repository under a dedicated
package:

```text
data_pipelines/sec_edgar/
├── README.md
├── config/
├── mappings/
├── src/sec_edgar_pipeline/
├── scripts/
└── tests/
```

Mutable and potentially large runtime data live under ignored private storage:

```text
private/sec_edgar/
├── raw/
├── normalized/
├── releases/
└── reports/
```

The Alpha project receives one thin read-only adapter,
`SecFundamentalProvider`. It may verify a release manifest, load canonical
datasets, perform an as-of lookup, and expose field panels. It must not download
SEC data, parse XBRL, resolve tags, interpret filing periods, or handle
restatements.

No raw SEC payload is placed in an Alpha Library directory or committed to Git.

## 5. Acquisition contract

### 5.1 Sources

Use only official SEC endpoints:

```text
data.sec.gov/api/xbrl/companyfacts/CIK##########.json
data.sec.gov/submissions/CIK##########.json
```

Follow `files` references from the submissions response when older filing
history is needed for 2019 onward.

Join Company Facts observations to Submissions metadata by accession number to
obtain the acceptance timestamp. A missing or conflicting accession join cannot
be repaired by using `period_end`; it follows the conservative date-only or
unresolved path.

### 5.2 Request behavior

- Send a declared SEC-compliant User-Agent.
- Use a conservative client limit below the SEC maximum request rate.
- Retry only transient errors with bounded exponential backoff.
- Honor `Retry-After` for throttling responses.
- Never require an SEC API key.
- Never write request headers or contact configuration into delivered
  manifests.

### 5.3 Immutable raw storage

Every raw response is stored unchanged with:

```text
source_type
cik
source_url
requested_at
first_seen_at
http_status
size_bytes
sha256
pipeline_version
```

Existing raw artifacts are never overwritten. A repeated identical response
may reuse its content-addressed object while recording a new observation in the
download ledger. Interrupted runs resume without redownloading verified files.

## 6. Issuer and security identity

CIK is an issuer identity, not a complete security identity. Maintain an
effective-dated bridge with at least:

```text
security_id
issuer_cik
ticker
share_class
security_type
exchange
effective_from
effective_to
mapping_source
mapping_status
```

Allowed mapping states are:

```text
VERIFIED_SINGLE_CLASS
VERIFIED_ENTITY_LEVEL_ONLY
AMBIGUOUS_MULTI_CLASS
UNRESOLVED
DEFERRED_ISSUER_SCOPE
```

Entity-level statement facts may map to a project security only when the
issuer/security bridge is unambiguous for the requested use. Entity facts must
not be silently duplicated into multiple share classes. Share-class ambiguity
blocks per-share, share-count, market-cap, and enterprise-value proxies.

## 7. Normalized fact-version model

`fundamental_versions.parquet` is the canonical historical truth table. It
preserves every filing version rather than overwriting old values.

Required columns include:

```text
issuer_cik
security_id
ticker_at_source

xbrl_namespace
xbrl_tag
canonical_field
mapping_version
mapping_status

value
unit
decimals

period_start
period_end
fact_type
duration_days
fiscal_year
fiscal_period
reported_basis

form_type
accession_number
filed_date
acceptance_datetime
historical_available_at
first_seen_at
available_at
availability_precision
amendment_flag

frame
dimensions_signature
source_fact_id
raw_source_hash
pipeline_version
```

For Company Facts V1, accepted observations must use standard taxonomies and
apply to the entire filing entity. Unsupported dimensions or custom extensions
remain unresolved rather than being flattened into entity-level values.

## 8. Point-in-time availability

For a fact with an exact acceptance timestamp:

```text
historical_available_at = acceptance_datetime + 10 minutes
```

For forward operation:

```text
available_at = max(historical_available_at, first_seen_at)
```

Historical diagnostic panels use `historical_available_at`. Forward panels use
`available_at`, so a forward process cannot claim access before it actually
observed the filing.

For a date-only record, availability begins at the first signal cutoff on the
next exchange session after the filing date. `period_end` is never an
availability timestamp.

A fact may enter a panel only when:

```text
available_at <= signal_cutoff
```

The implementation uses the project's exchange calendar. A filing that becomes
available after a close cannot enter that close's signal.

## 9. Period and basis semantics

Raw facts remain unchanged. Derived accounting bases are materialized only when
their prerequisites are compatible by issuer, canonical field, unit, fiscal
calendar, and period boundaries.

### 9.1 Supported bases

- `RAW_REPORTED`: value exactly as filed.
- `INSTANT`: balance-sheet fact at one period end.
- `QUARTERLY_DISCRETE`: one fiscal quarter's flow.
- `ANNUAL`: complete fiscal-year flow.
- `TTM`: sum of the latest four PIT-visible discrete quarters.

### 9.2 Quarter derivation

Quarterly derivation may use compatible cumulative facts:

```text
Q1 = reported Q1
Q2 = reported H1 - Q1
Q3 = reported 9M - H1
Q4 = reported FY - reported 9M
```

The calculation is rejected if fiscal starts, units, contexts, taxonomy
meanings, or filing versions are incompatible. A later restatement changes the
derived value only after that restatement becomes available.

### 9.3 Mapping binding

Each BRAIN field mapping declares one required basis. A runtime must never
choose between quarterly, annual, and TTM values based on availability or
performance.

## 10. Demand-driven canonical field catalog

V1 attempts explicit standard-tag mappings for the following domains.
Availability is not guaranteed merely because a field appears in the catalog.

### 10.1 Balance sheet

```text
total_assets
total_liabilities
stockholders_equity
current_assets
current_liabilities
cash_and_cash_equivalents
inventory
accounts_receivable
goodwill
short_term_debt
long_term_debt_current
long_term_debt_noncurrent
deferred_tax_assets
deferred_tax_liabilities
deferred_tax_valuation_allowance
```

### 10.2 Income statement

```text
revenue
gross_profit
operating_income
net_income
research_and_development_expense
interest_expense
income_tax_expense
depreciation_and_amortization
```

### 10.3 Cash flow

```text
operating_cash_flow
capital_expenditure
dividends_paid
income_taxes_paid
```

### 10.4 Shares and per-share facts

```text
shares_outstanding_sec
weighted_average_basic_shares
weighted_average_diluted_shares
basic_eps
diluted_eps
```

### 10.5 Explicit mapping policy

The mapping table records taxonomy, tag, unit, statement, basis, priority,
effective dates, and validation evidence. Mapping is allowed only through an
explicit whitelist. Fuzzy matching, LLM-only name similarity, synthetic zeros,
and silent fallback between economically different tags are forbidden.

Standard Company Facts that cannot be mapped safely receive
`DEFERRED_SEMANTIC`. Facts available only through custom extensions receive
`DEFERRED_CUSTOM_TAG`.

## 11. Deterministic derived-field layer

Derived fields live in a separate versioned catalog with their formula,
required inputs, basis, unit, sign convention, and missing-value rule.

V1 may attempt:

```text
total_debt
working_capital
free_cash_flow
ebit
ebitda
roa
roe
book_value_per_share
asset_growth
revenue_growth
inventory_turnover
market_cap_sec_proxy
enterprise_value_sec_proxy
```

Derived fields are created only when every required input is PIT-visible and
semantically approved. Missing inputs produce missing output. No zero
substitution is allowed.

`shares_outstanding_sec`, `market_cap_sec_proxy`, and
`enterprise_value_sec_proxy` are proxy-grade fields because SEC share counts do
not establish parity with BRAIN daily shares or market capitalization.

## 12. Daily PIT panel

`daily_pit_fundamental_panel.parquet` is generated on the project's exchange
sessions. For each security, field, basis, and session cutoff, it selects the
latest valid PIT-visible value for the relevant reporting period.

Once a filing becomes available, its value may carry forward to later sessions
until a newer eligible filing version replaces it. Values are never filled
backward before first availability.

The daily panel preserves the meaning of BRAIN time-series windows. A later
consumer may sample weekly sessions, but the data pipeline does not reinterpret
daily window lengths as weeks.

## 13. Main-project adapter contract

`SecFundamentalProvider` accepts a pinned dataset version and manifest path. It:

1. verifies every declared file hash;
2. rejects an unapproved validation status;
3. loads requested canonical or derived fields;
4. returns DataFrame panels indexed by exchange session with `security_id`
   columns;
5. exposes dataset, mapping, PIT-contract, and field-definition hashes.

The adapter must fail closed on missing files, hash mismatch, unknown field,
unknown basis, or ambiguous identity mapping.

## 14. Dual-track Alpha reconstruction

### 14.1 `SEC_SOURCE_LINKED`

A source may enter the strict track only when:

- every required unavailable field has a reviewed direct or deterministic
  derived mapping;
- required period bases are explicit;
- every operator is supported without an unrecorded semantic substitution;
- security identity is unambiguous;
- the formula compiles and computes on the daily PIT panel;
- prefix-invariance and no-future-data checks pass.

Unsupported group operations, missing PIT classifications, daily `sharesout`,
daily `cap`, analyst expectations, option data, news, social data, and vendor
model scores prevent strict admission.

### 14.2 `SEC_HYPOTHESIS_PROXY`

A source may enter the proxy track when either:

1. its fundamental component is mathematically separable under the existing
   performance-blind reconstruction policy; or
2. a related fundamental hypothesis is explicitly defined and frozen before
   evaluation.

Every proxy records:

```text
parent_source_alpha_ids
original_expression_hash
proxy_expression
proxy_expression_hash
retained_economic_hypothesis
discarded_information_domains
semantic_losses
required_fields
```

A proxy must never be counted as an exact BRAIN reconstruction. Performance
results cannot be used to decide how a proxy is defined.

### 14.3 Status dimensions

Keep engineering, semantics, coverage, and usage separate:

```text
build_status: DEFERRED_DATA | FIELD_MAPPED | COMPILED | COMPUTE_VERIFIED
semantic_status: SOURCE_LINKED | HYPOTHESIS_PROXY | UNRESOLVED
coverage_status: NOT_MEASURED | MEASURED | LIMITED
usage_tier: DIAGNOSTIC_ONLY
```

`COMPUTE_VERIFIED` proves deterministic computation and timing checks only. It
does not prove Alpha effectiveness, adequate coverage, or production fitness.
Coverage statistics are reported without adding or tuning a new selection
threshold in this phase.

## 15. Deduplication and library assembly

Normalize compiled expressions to a canonical AST and hash the normalized
formula plus semantic field bindings and factor settings.

Deduplicate against:

1. V4's 135 immutable formulas;
2. other new source-linked formulas;
3. new proxy formulas.

If a new source maps to an existing V4 formula, retain the existing factor ID
and add the new source relationship only in the expansion registry. Do not edit
the V4 artifact.

If source-linked and proxy candidates produce the same normalized formula,
store one formula and preserve both provenance records while retaining the more
conservative semantic label for each source relationship.

The delivery reports separately:

```text
deferred sources examined
strict source candidates
strict sources unlocked
proxy sources represented
compiled candidates
compute-verified candidates
duplicates against V4
duplicates within the expansion
new unique formulas
final expanded unique-formula count
```

No minimum new-factor count is promised.

## 16. Validation strategy

### 16.1 Deterministic fixtures

Tests must cover:

- a future filing excluded from an earlier signal;
- an acceptance timestamp within ten minutes of market close;
- a filing accepted after close;
- a date-only filing;
- amendment/restatement isolation;
- Q1, H1, 9M, and FY conversion to discrete quarters;
- incompatible cumulative periods rejected;
- TTM construction using only visible discrete quarters;
- instant versus duration facts;
- units and sign conventions;
- missing inputs remaining missing;
- issuer/security identity and multi-class ambiguity;
- custom tags remaining deferred;
- raw and processed hash determinism;
- source lineage back to accession, tag, and raw hash;
- factor prefix invariance;
- formula deduplication and V4 immutability.

### 16.2 Manual issuer validation

Before a 500-security run, validate representative issuers including AAPL,
MSFT, JPM, XOM, and WMT. Add a deterministic multi-class fixture and a known
amendment/restatement fixture even if no convenient live example appears in the
five issuers.

The manual report reconciles selected values to the underlying SEC filing,
checks period/basis transformations, confirms availability timing, and records
unresolved mappings.

### 16.3 Scale gate

The pipeline may process the 500-stock development universe only after fixture
tests and manual validation pass. The scaled run reports field-by-field and
factor-by-factor coverage. It retains:

```text
historical_pit_universe_verified = false
survivorship_bias = true
selection_evidence = DEVELOPMENT_EXPOSED
```

Correct PIT fundamentals do not repair current-universe survivorship bias.

## 17. Versioning and manifests

Any change to field mapping, derived formula, PIT timing, identity mapping,
period derivation, unit normalization, or restatement handling creates a new
dataset version.

Each release manifest includes:

```text
dataset_version
pipeline_commit
created_at
universe_snapshot_hash
raw_manifest_hash
canonical_mapping_hash
derived_field_catalog_hash
security_mapping_hash
pit_contract_hash
date_min
date_max
security_count
field_count
record_count
provider
historical_pit_universe_verified
validation_status
output_files
output_hashes
```

Mutable `latest` files are not valid dependencies. Consumers pin a version and
hashes.

## 18. Deliverables

### 18.1 Dataset release

```text
SEC_PIT_FUNDAMENTALS_V1/
├── fundamental_versions.parquet
├── daily_pit_fundamental_panel.parquet
├── canonical_field_catalog.csv
├── derived_field_definitions.json
├── cik_security_map.csv
├── validation_report.md
├── coverage_report.csv
├── PIT_DATASET_README.md
└── processed_manifest.json
```

### 18.2 Alpha expansion release

```text
EXPANDED_ALPHA_LIBRARY_V1/
├── expanded_alpha_library.json
├── fundamental_deferred_source_map.csv
├── sec_source_linked_factors.json
├── sec_hypothesis_proxy_factors.json
├── source_reconstruction_index.csv
├── deduplication_report.csv
├── conversion_report.json
├── validation_report.md
└── manifest.json
```

The current-library pointer is not changed automatically. The user reviews the
expanded release before any pointer update or downstream filtering.

## 19. Implementation sequence

```text
P0  Freeze demand matrix, field catalog, schemas, and PIT contract
P1  Implement immutable per-CIK acquisition and manifests
P2  Implement identity, normalization, mapping, period bases, and derived fields
P3  Implement daily PIT panel and SecFundamentalProvider
P4  Complete deterministic tests and manual issuer validation
P5  Run the 500-stock dataset build and coverage audit
P6  Revisit deferred sources using the two approved tracks
P7  Compile, compute-verify, deduplicate, and assemble Expanded Library V1
P8  Produce final manifests and review report
```

No step may skip ahead from raw acquisition to Alpha reconstruction before the
PIT validation gate passes.

## 20. Stop conditions

Stop and report rather than improvising when:

- issuer/security identity is ambiguous;
- a required fact exists only as an unsupported custom tag;
- field or period semantics are ambiguous;
- units or sign conventions cannot be reconciled;
- acceptance availability cannot be established conservatively;
- a restatement lineage is unclear;
- a cumulative period cannot be converted safely;
- a factor produces no valid cross-sectional diagnostic observations; factors
  with nonzero but limited coverage remain in the report with
  `coverage_status=LIMITED` rather than being silently dropped;
- an operator requires a missing PIT classification or unsupported data domain.

Do not substitute Yahoo fundamentals, analyst estimates, third-party
fundamentals, price proxies, synthetic zeros, or backfilled future values.

## 21. Explicit non-goals

This phase does not:

- alter the V4 135-factor library;
- alter the V5.1 Lite filter or its 8-15 factor contract;
- tune thresholds based on expanded-library performance;
- create Challenger C;
- start a forward clock;
- certify a historical PIT universe;
- implement custom XBRL parsing;
- download the all-SEC bulk archive;
- submit broker orders or call broker execution endpoints.

The terminal result is a reviewable, versioned expanded personal Alpha library
whose new formulas have explicit SEC lineage, point-in-time timing, semantic
classification, and deterministic build evidence.
