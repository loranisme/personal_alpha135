# SEC EDGAR PIT Data Product Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build an immutable, point-in-time SEC Company Facts data product for the frozen current 500-stock development universe and publish the reviewed `SEC_PIT_FUNDAMENTALS_V1` release for downstream Alpha reconstruction.

**Architecture:** A standalone `data_pipelines/sec_edgar` package downloads official per-CIK SEC payloads into a content-addressed raw store, resolves issuer/security identity, normalizes versioned facts, applies explicit standard-tag mappings, derives accounting periods and fields, and materializes daily exchange-session panels. The main project receives only a fail-closed `SecFundamentalProvider` that reads a pinned, hash-verified release.

**Tech Stack:** Python 3.12, pandas, NumPy, PyArrow, requests, exchange-calendars, pytest, JSON/CSV/Parquet artifacts, SHA-256 manifests.

**Spec:** `docs/superpowers/specs/2026-09-17-sec-edgar-pit-fundamentals-design.md`

## Global Constraints

- Use official SEC resources only. The ticker-to-CIK bootstrap uses `www.sec.gov/files/company_tickers_exchange.json`; fundamental observations come only from per-CIK `data.sec.gov` Company Facts and Submissions resources for the supplied 500-security snapshot.
- Persist a declared SEC-compliant User-Agent in local runtime configuration, but never write it or request headers into release artifacts.
- Support U.S. domestic US-GAAP ordinary shares and REITs with `10-K`, `10-Q`, `10-K/A`, and `10-Q/A`; fail closed on ADR, IFRS, `20-F`, `40-F`, and ambiguous share-class cases.
- Historical availability is exact SEC acceptance time plus ten minutes. A date-only filing becomes visible at the next XNYS signal cutoff. Forward availability is the later of historical availability and actual first observation time.
- Use an explicit whitelist for standard taxonomy tags. Never use fuzzy tag matching, custom XBRL extraction, HTML number extraction, synthetic zeros, or future-filled values.
- Preserve raw, instant, quarterly-discrete, annual, and TTM meanings. Never choose a period basis from observed performance.
- Treat SEC share counts, SEC-derived market capitalization, and SEC-derived enterprise value as proxy-grade semantics.
- Build daily PIT panels before any weekly sampling.
- Keep mutable runtime data under ignored `private/sec_edgar`; commit only code, configuration, fixtures, and documentation.
- Do not modify the V4 Alpha library, V5.1 filter, Champion, PRE2025 pool, current-library pointer, or broker path.
- A 500-security run is blocked until deterministic fixtures and the five-issuer manual validation gate pass.

---

### Task 1: Freeze Package, Policy, Schemas, and Hash Contracts

**Files:**
- Create: `data_pipelines/sec_edgar/pyproject.toml`
- Create: `data_pipelines/sec_edgar/README.md`
- Create: `data_pipelines/sec_edgar/config/sec_pipeline.json`
- Create: `data_pipelines/sec_edgar/config/derived_fields.json`
- Create: `data_pipelines/sec_edgar/src/sec_edgar_pipeline/__init__.py`
- Create: `data_pipelines/sec_edgar/src/sec_edgar_pipeline/contracts.py`
- Create: `data_pipelines/sec_edgar/src/sec_edgar_pipeline/hashing.py`
- Create: `data_pipelines/sec_edgar/tests/test_contracts.py`
- Create: `data_pipelines/sec_edgar/tests/test_hashing.py`

**Interfaces:**

```text
@dataclass(frozen=True)
class SecPipelinePolicy:
    schema_version: int
    provider: str
    supported_namespaces: tuple[str, ...]
    supported_forms: tuple[str, ...]
    availability_buffer_minutes: int
    exchange_calendar: str
    historical_signal_cutoff: str
    maximum_requests_per_second: float
    start_date: str


def load_policy(path: Path | str) -> SecPipelinePolicy
def canonical_json_hash(value: Any) -> str
def sha256_file(path: Path | str) -> str
def write_json_exclusive(path: Path | str, payload: Mapping[str, Any]) -> Path
def verify_output_hashes(root: Path | str, output_hashes: Mapping[str, str]) -> None
```

- [ ] **Step 1: Write failing policy and hashing tests**

```python
from pathlib import Path
import json
import pytest
from sec_edgar_pipeline.contracts import load_policy
from sec_edgar_pipeline.hashing import canonical_json_hash, sha256_file, verify_output_hashes, write_json_exclusive


def test_policy_freezes_approved_timing_and_scope(tmp_path: Path):
    path = tmp_path / "policy.json"
    path.write_text(json.dumps({
        "schema_version": 1,
        "provider": "SEC_EDGAR_COMPANYFACTS",
        "supported_namespaces": ["us-gaap", "dei"],
        "supported_forms": ["10-K", "10-K/A", "10-Q", "10-Q/A"],
        "availability_buffer_minutes": 10,
        "exchange_calendar": "XNYS",
        "historical_signal_cutoff": "16:00 America/New_York",
        "maximum_requests_per_second": 5.0,
        "start_date": "2019-01-01"
    }))
    policy = load_policy(path)
    assert policy.availability_buffer_minutes == 10
    assert policy.supported_forms == ("10-K", "10-K/A", "10-Q", "10-Q/A")


def test_policy_rejects_changed_buffer(tmp_path: Path):
    source = Path("data_pipelines/sec_edgar/config/sec_pipeline.json")
    payload = json.loads(source.read_text())
    payload["availability_buffer_minutes"] = 0
    path = tmp_path / "policy.json"
    path.write_text(json.dumps(payload))
    with pytest.raises(ValueError, match="SEC_POLICY_AVAILABILITY_BUFFER_MUST_BE_TEN"):
        load_policy(path)


def test_exclusive_json_and_hash_verification_detect_tampering(tmp_path: Path):
    path = write_json_exclusive(tmp_path / "record.json", {"b": 2, "a": 1})
    expected = sha256_file(path)
    verify_output_hashes(tmp_path, {"record.json": expected})
    path.write_text('{"a":9}\n')
    with pytest.raises(ValueError, match="OUTPUT_HASH_MISMATCH:record.json"):
        verify_output_hashes(tmp_path, {"record.json": expected})
```

- [ ] **Step 2: Verify imports fail**

Run:

```bash
PYTHONPATH=data_pipelines/sec_edgar/src:src .venv/bin/pytest -q data_pipelines/sec_edgar/tests/test_contracts.py data_pipelines/sec_edgar/tests/test_hashing.py
```

Expected: test collection fails because `sec_edgar_pipeline` does not exist.

- [ ] **Step 3: Implement strict configuration parsing**

Write `sec_pipeline.json` with exactly the values in the first test plus:

```json
{
  "allowed_security_types": ["COMMON_STOCK", "REIT"],
  "domicile": "US",
  "taxonomy_policy": "STANDARD_ENTITY_WIDE_ONLY",
  "historical_availability_mode": "ACCEPTANCE_PLUS_TEN_MINUTES",
  "forward_availability_mode": "MAX_HISTORICAL_AND_FIRST_SEEN",
  "raw_store_mode": "CONTENT_ADDRESSED_APPEND_ONLY",
  "historical_pit_universe_verified": false,
  "selection_evidence": "DEVELOPMENT_EXPOSED"
}
```

Reject unknown keys, missing keys, altered approved constants, a request rate above five per second, a non-XNYS calendar, and a start date later than `2019-01-01`.

- [ ] **Step 4: Implement deterministic hashing and exclusive writes**

Use UTF-8 canonical JSON with sorted keys, compact separators, and `allow_nan=False`. `write_json_exclusive` must create parent directories and open the final file with mode `x`. `verify_output_hashes` must reject absolute paths, `..` path components, missing files, symlinks escaping the release root, and hash mismatches.

- [ ] **Step 5: Define the derived-field catalog contract**

Write `derived_fields.json` with versioned definitions for `total_debt`, `working_capital`, `free_cash_flow`, `ebit`, `ebitda`, `roa`, `roe`, `book_value_per_share`, `asset_growth`, `revenue_growth`, `inventory_turnover`, `market_cap_sec_proxy`, and `enterprise_value_sec_proxy`. Each record must contain `formula`, `required_inputs`, `required_basis`, `unit_rule`, `sign_convention`, `missing_value_rule`, and `semantic_grade`. Set the final three proxy-dependent definitions to `HYPOTHESIS_PROXY`; set deterministic accounting identities to `SOURCE_LINKED_DERIVED`.

- [ ] **Step 6: Run focused tests**

Run:

```bash
PYTHONPATH=data_pipelines/sec_edgar/src:src .venv/bin/pytest -q data_pipelines/sec_edgar/tests/test_contracts.py data_pipelines/sec_edgar/tests/test_hashing.py
```

Expected: all tests pass.

- [ ] **Step 7: Commit**

```bash
git add data_pipelines/sec_edgar
git commit -m "Define SEC PIT data product contracts"
```

---

### Task 2: Implement Official Per-CIK Acquisition and Immutable Raw Storage

**Files:**
- Create: `data_pipelines/sec_edgar/src/sec_edgar_pipeline/client.py`
- Create: `data_pipelines/sec_edgar/src/sec_edgar_pipeline/raw_store.py`
- Create: `data_pipelines/sec_edgar/src/sec_edgar_pipeline/issuer_seed.py`
- Create: `data_pipelines/sec_edgar/scripts/run_sec_download.py`
- Create: `data_pipelines/sec_edgar/tests/test_client.py`
- Create: `data_pipelines/sec_edgar/tests/test_raw_store.py`
- Create: `data_pipelines/sec_edgar/tests/test_issuer_seed.py`
- Create: `data_pipelines/sec_edgar/tests/fixtures/sec/companyfacts_aapl_minimal.json`
- Create: `data_pipelines/sec_edgar/tests/fixtures/sec/submissions_aapl_minimal.json`

**Interfaces:**

```text
@dataclass(frozen=True)
class RawArtifact:
    source_type: str
    cik: str
    source_url: str
    requested_at: str
    first_seen_at: str
    http_status: int
    size_bytes: int
    sha256: str
    object_path: str
    pipeline_version: str


def normalize_cik(value: str | int) -> str
def load_ticker_cik_index(payload: Mapping[str, Any], raw_hash: str) -> pd.DataFrame
def resolve_universe_ciks(universe: pd.DataFrame, ticker_cik_index: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]

class SecClient:
    def __init__(self, session: requests.Session, user_agent: str, maximum_requests_per_second: float = 5.0)
    def get_json(self, url: str) -> tuple[bytes, int]


def store_raw_response(root: Path | str, artifact: RawArtifact, content: bytes) -> Path
def download_cik_bundle(client: SecClient, raw_root: Path | str, cik: str, observed_at: datetime) -> list[RawArtifact]
def download_universe(client: SecClient, raw_root: Path | str, universe: pd.DataFrame, observed_at: datetime) -> pd.DataFrame
```

- [ ] **Step 1: Write failing downloader tests with a fake session**

```python
class FakeResponse:
    def __init__(self, status: int, body: bytes, headers: dict[str, str] | None = None):
        self.status_code = status
        self.content = body
        self.headers = headers or {}


class FakeSession:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.calls = []
    def get(self, url, headers, timeout):
        self.calls.append((url, headers, timeout))
        return next(self.responses)


def test_download_uses_official_per_cik_endpoints_and_declared_header(tmp_path):
    body = b'{"cik":320193,"facts":{}}'
    session = FakeSession([FakeResponse(200, body), FakeResponse(200, b'{"cik":"0000320193"}')])
    client = SecClient(session, "PersonalAlpha contact@example.com")
    rows = download_cik_bundle(client, tmp_path, "320193", datetime(2026, 9, 17, tzinfo=timezone.utc))
    assert [row.source_type for row in rows] == ["companyfacts", "submissions"]
    assert session.calls[0][0] == "https://data.sec.gov/api/xbrl/companyfacts/CIK0000320193.json"
    assert session.calls[1][0] == "https://data.sec.gov/submissions/CIK0000320193.json"
    assert session.calls[0][1]["User-Agent"] == "PersonalAlpha contact@example.com"


def test_same_content_reuses_object_but_appends_observation(tmp_path):
    artifact = RawArtifact("companyfacts", "0000320193", "https://data.sec.gov/a", "2026-09-17T00:00:00Z", "2026-09-17T00:00:00Z", 200, 2, hashlib.sha256(b"{}").hexdigest(), "", "1")
    first = store_raw_response(tmp_path, artifact, b"{}")
    second = store_raw_response(tmp_path, replace(artifact, requested_at="2026-09-17T01:00:00Z"), b"{}")
    assert first == second
    assert len((tmp_path / "download_ledger.jsonl").read_text().splitlines()) == 2
```

Also test `429` honoring `Retry-After`, bounded retry for `500/502/503/504`, no retry for `400/403/404`, invalid JSON rejection, truncated response rejection, and resume from a verified object.

```python
def test_official_ticker_index_resolves_each_download_cik_once():
    payload = {"data": [["0000320193", "Apple Inc.", "AAPL", "Nasdaq"]], "fields": ["cik", "name", "ticker", "exchange"]}
    index = load_ticker_cik_index(payload, "a" * 64)
    universe = pd.DataFrame([{"security_id": "sec-aapl", "ticker": "AAPL", "exchange": "NASDAQ", "security_type": "COMMON_STOCK"}])
    resolved, unresolved = resolve_universe_ciks(universe, index)
    assert unresolved.empty
    assert resolved.iloc[0].issuer_cik == "0000320193"
    assert resolved.iloc[0].ticker_cik_source_hash == "a" * 64
```

- [ ] **Step 2: Verify tests fail**

Run:

```bash
PYTHONPATH=data_pipelines/sec_edgar/src:src .venv/bin/pytest -q data_pipelines/sec_edgar/tests/test_client.py data_pipelines/sec_edgar/tests/test_raw_store.py data_pipelines/sec_edgar/tests/test_issuer_seed.py
```

Expected: imports fail for `client`, `raw_store`, and `issuer_seed`.

- [ ] **Step 3: Implement official ticker-to-CIK bootstrap and rate-limited SEC client**

Fetch `https://www.sec.gov/files/company_tickers_exchange.json` once per run, store it through the same immutable raw-object and observation ledger, and resolve the supplied universe by normalized ticker and compatible exchange. Preserve the raw index hash on every resolved row. A missing, duplicate, or conflicting match is emitted to `unresolved_universe_ciks.csv` and is not downloaded. This bootstrap supplies issuer identifiers only and must never supply fundamental values.

Send only `Accept-Encoding`, `Host`, and the configured `User-Agent`. Use connect/read timeouts, monotonic pacing capped at five requests per second, bounded exponential backoff, and `Retry-After`. Do not persist headers. Validate the body as one complete JSON object before storage.

- [ ] **Step 4: Implement content-addressed raw store and resume ledger**

Store bytes under the exact pattern `raw/objects/{sha256[:2]}/{sha256}.json`. Append one observation per request to `download_ledger.jsonl` through a temporary file plus atomic rename of the extended ledger. Store `companyfacts`, `submissions`, and every referenced older submissions file as distinct observations. A resume may skip a URL only when its ledger row points to an existing object whose hash and size both match.

- [ ] **Step 5: Add finite download CLI**

`run_sec_download.py` must require `--universe-csv`, `--raw-root`, `--user-agent-env`, and `--observed-at`. It first resolves the universe against the immutable official SEC ticker-to-CIK index and then requests each unique resolved CIK exactly once. Resolve the User-Agent from the named environment variable, print no credential or header values, require an empty or resumable raw root, and emit a JSON summary with requested CIK count, completed CIK count, unresolved ticker count, HTTP status counts, and raw-manifest hash.

- [ ] **Step 6: Run focused tests**

Run:

```bash
PYTHONPATH=data_pipelines/sec_edgar/src:src .venv/bin/pytest -q data_pipelines/sec_edgar/tests/test_client.py data_pipelines/sec_edgar/tests/test_raw_store.py data_pipelines/sec_edgar/tests/test_issuer_seed.py
```

Expected: all tests pass without network access.

- [ ] **Step 7: Commit**

```bash
git add data_pipelines/sec_edgar/src/sec_edgar_pipeline/client.py data_pipelines/sec_edgar/src/sec_edgar_pipeline/raw_store.py data_pipelines/sec_edgar/src/sec_edgar_pipeline/issuer_seed.py data_pipelines/sec_edgar/scripts/run_sec_download.py data_pipelines/sec_edgar/tests
git commit -m "Add immutable SEC per-CIK acquisition"
```

---

### Task 3: Resolve Effective-Dated Issuer and Security Identity

**Files:**
- Create: `data_pipelines/sec_edgar/src/sec_edgar_pipeline/identity.py`
- Create: `data_pipelines/sec_edgar/tests/test_identity.py`
- Create: `data_pipelines/sec_edgar/tests/fixtures/sec/submissions_multiclass.json`
- Create: `data_pipelines/sec_edgar/tests/fixtures/sec/universe_identity.csv`

**Interfaces:**

```text
MAPPING_STATES = {
    "VERIFIED_SINGLE_CLASS",
    "VERIFIED_ENTITY_LEVEL_ONLY",
    "AMBIGUOUS_MULTI_CLASS",
    "UNRESOLVED",
    "DEFERRED_ISSUER_SCOPE",
}


def build_cik_security_map(
    universe: pd.DataFrame,
    submissions_by_cik: Mapping[str, Mapping[str, Any]],
    as_of: pd.Timestamp,
) -> pd.DataFrame


def require_usable_identity(mapping: pd.DataFrame, security_id: str, usage: str) -> pd.Series
```

- [ ] **Step 1: Write failing identity tests**

```python
def test_single_common_share_maps_to_verified_single_class():
    universe = pd.DataFrame([{"security_id": "sec-aapl", "ticker": "AAPL", "exchange": "NASDAQ", "security_type": "COMMON_STOCK", "as_of": "2026-09-15T09:24:13Z"}])
    submissions = {"0000320193": {"cik": "0000320193", "tickers": ["AAPL"], "exchanges": ["Nasdaq"]}}
    result = build_cik_security_map(universe, submissions, pd.Timestamp("2026-09-15T09:24:13Z"))
    assert result.iloc[0].mapping_status == "VERIFIED_SINGLE_CLASS"
    assert result.iloc[0].issuer_cik == "0000320193"


def test_two_project_securities_for_one_cik_block_share_class_usage():
    universe = pd.DataFrame([
        {"security_id": "sec-a", "ticker": "BRK.A", "exchange": "NYSE", "security_type": "COMMON_STOCK", "as_of": "2026-09-15T09:24:13Z"},
        {"security_id": "sec-b", "ticker": "BRK.B", "exchange": "NYSE", "security_type": "COMMON_STOCK", "as_of": "2026-09-15T09:24:13Z"},
    ])
    submissions = {"0001067983": {"cik": "0001067983", "tickers": ["BRK-A", "BRK-B"], "exchanges": ["NYSE", "NYSE"]}}
    result = build_cik_security_map(universe, submissions, pd.Timestamp("2026-09-15T09:24:13Z"))
    assert set(result.mapping_status) == {"AMBIGUOUS_MULTI_CLASS"}
    with pytest.raises(ValueError, match="IDENTITY_NOT_USABLE:PER_SHARE"):
        require_usable_identity(result, "sec-a", "PER_SHARE")
```

Also assert ADR, foreign-form, non-US domicile, unsupported security type, missing ticker, and conflicting exchange cases resolve to explicit blocked states rather than guessed mappings.

- [ ] **Step 2: Verify tests fail**

Run:

```bash
PYTHONPATH=data_pipelines/sec_edgar/src:src .venv/bin/pytest -q data_pipelines/sec_edgar/tests/test_identity.py
```

Expected: import fails for `identity`.

- [ ] **Step 3: Implement normalized ticker matching and issuer scope checks**

Normalize SEC hyphen notation and provider dot notation only for comparison. Preserve both original values. Require one issuer match, compatible exchange, supported security type, U.S. domicile, and no foreign filing-form evidence. Never copy entity facts to two project security IDs when a per-share, shares, market-cap, or enterprise-value use is requested.

- [ ] **Step 4: Materialize exact identity schema**

Write rows with `security_id`, `issuer_cik`, `ticker`, `sec_ticker`, `share_class`, `security_type`, `exchange`, `effective_from`, `effective_to`, `mapping_source`, `mapping_status`, `blocking_reasons`, and `source_submission_hash`. Sort by `security_id`, `effective_from`, and `issuer_cik` before hashing.

- [ ] **Step 5: Run focused tests**

Run:

```bash
PYTHONPATH=data_pipelines/sec_edgar/src:src .venv/bin/pytest -q data_pipelines/sec_edgar/tests/test_identity.py
```

Expected: all tests pass.

- [ ] **Step 6: Commit**

```bash
git add data_pipelines/sec_edgar/src/sec_edgar_pipeline/identity.py data_pipelines/sec_edgar/tests/test_identity.py data_pipelines/sec_edgar/tests/fixtures/sec
git commit -m "Add effective-dated SEC identity bridge"
```

---

### Task 4: Normalize Fact Versions and Enforce PIT Availability

**Files:**
- Create: `data_pipelines/sec_edgar/src/sec_edgar_pipeline/submissions.py`
- Create: `data_pipelines/sec_edgar/src/sec_edgar_pipeline/availability.py`
- Create: `data_pipelines/sec_edgar/src/sec_edgar_pipeline/normalize.py`
- Create: `data_pipelines/sec_edgar/tests/test_submissions.py`
- Create: `data_pipelines/sec_edgar/tests/test_availability.py`
- Create: `data_pipelines/sec_edgar/tests/test_normalize.py`
- Create: `data_pipelines/sec_edgar/tests/fixtures/sec/companyfacts_period_cases.json`
- Create: `data_pipelines/sec_edgar/tests/fixtures/sec/submissions_period_cases.json`

**Interfaces:**

```text
def build_accession_index(submissions: Mapping[str, Any], older_files: Sequence[Mapping[str, Any]]) -> pd.DataFrame
def historical_available_at(acceptance: str | None, filed_date: str, calendar_name: str, cutoff_local_time: str) -> tuple[pd.Timestamp, str]
def forward_available_at(historical_time: pd.Timestamp, first_seen_at: pd.Timestamp) -> pd.Timestamp
def normalize_companyfacts(companyfacts: Mapping[str, Any], accession_index: pd.DataFrame, identity_row: Mapping[str, Any], raw_source_hash: str, first_seen_at: pd.Timestamp, pipeline_version: str) -> pd.DataFrame
```

- [ ] **Step 1: Write failing timing tests**

```python
def test_exact_acceptance_adds_ten_minutes():
    value, precision = historical_available_at("2026-02-03T20:50:00Z", "2026-02-03", "XNYS", "16:00")
    assert value == pd.Timestamp("2026-02-03T21:00:00Z")
    assert precision == "EXACT_ACCEPTANCE"


def test_date_only_record_uses_next_session_cutoff():
    value, precision = historical_available_at(None, "2026-07-03", "XNYS", "16:00")
    assert value == pd.Timestamp("2026-07-06T20:00:00Z")
    assert precision == "DATE_ONLY_NEXT_SESSION_CUTOFF"


def test_forward_time_cannot_precede_first_observation():
    historical = pd.Timestamp("2026-02-03T21:00:00Z")
    observed = pd.Timestamp("2026-02-04T13:00:00Z")
    assert forward_available_at(historical, observed) == observed
```

- [ ] **Step 2: Write failing normalization tests**

Assert one output row per Company Facts observation; accession joins preserve exact acceptance time; amendments remain separate versions; `period_start`, `period_end`, `duration_days`, `fact_type`, units, decimals, filing form, fiscal year/period, frame, accession, source fact ID, and raw hash survive normalization; custom namespaces and dimensional facts receive deferred statuses.

```python
def test_amendment_does_not_overwrite_original(normalized_period_cases):
    rows = normalized_period_cases.query("accession_number in ['0001-24-000001', '0001-24-000099']")
    assert len(rows) == 2
    assert rows.amendment_flag.tolist() == [False, True]
    assert rows.source_fact_id.nunique() == 2
    assert rows.historical_available_at.is_monotonic_increasing
```

- [ ] **Step 3: Verify tests fail**

Run:

```bash
PYTHONPATH=data_pipelines/sec_edgar/src:src .venv/bin/pytest -q data_pipelines/sec_edgar/tests/test_submissions.py data_pipelines/sec_edgar/tests/test_availability.py data_pipelines/sec_edgar/tests/test_normalize.py
```

Expected: imports fail for the new modules.

- [ ] **Step 4: Implement accession index and conservative availability**

Flatten recent and referenced older submission files into a unique accession index. Conflicting exact acceptance timestamps mark the accession `UNRESOLVED_ACCEPTANCE_CONFLICT`. Missing exact timestamps use the filed-date next-session rule. Never use `period_end` as an availability timestamp.

- [ ] **Step 5: Implement canonical fact-version rows**

Emit every column in section 7 of the approved spec. Accept only supported forms and entity-wide contexts. Set `mapping_status=DEFERRED_CUSTOM_TAG` for nonstandard namespaces and `mapping_status=DEFERRED_DIMENSION` for facts with unsupported dimensions. Do not drop these rows from lineage; exclude them only from mapped outputs.

- [ ] **Step 6: Run focused tests**

Run:

```bash
PYTHONPATH=data_pipelines/sec_edgar/src:src .venv/bin/pytest -q data_pipelines/sec_edgar/tests/test_submissions.py data_pipelines/sec_edgar/tests/test_availability.py data_pipelines/sec_edgar/tests/test_normalize.py
```

Expected: all tests pass.

- [ ] **Step 7: Commit**

```bash
git add data_pipelines/sec_edgar/src/sec_edgar_pipeline data_pipelines/sec_edgar/tests
git commit -m "Normalize versioned SEC facts with PIT timing"
```

---

### Task 5: Apply Explicit Canonical Field Mappings

**Files:**
- Create: `data_pipelines/sec_edgar/mappings/canonical_fields.csv`
- Create: `data_pipelines/sec_edgar/src/sec_edgar_pipeline/mapping.py`
- Create: `data_pipelines/sec_edgar/tests/test_mapping.py`

**Interfaces:**

```text
def load_mapping_catalog(path: Path | str) -> pd.DataFrame
def apply_canonical_mapping(raw_facts: pd.DataFrame, catalog: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]
```

- [ ] **Step 1: Write failing mapping tests**

```python
def test_assets_maps_only_from_reviewed_standard_tag(mapping_catalog):
    facts = pd.DataFrame([
        {"xbrl_namespace": "us-gaap", "xbrl_tag": "Assets", "unit": "USD", "fact_type": "INSTANT", "value": 10.0, "mapping_status": "UNMAPPED"},
        {"xbrl_namespace": "custom", "xbrl_tag": "AdjustedAssets", "unit": "USD", "fact_type": "INSTANT", "value": 99.0, "mapping_status": "DEFERRED_CUSTOM_TAG"},
    ])
    mapped, deferred = apply_canonical_mapping(facts, mapping_catalog)
    assert mapped.iloc[0].canonical_field == "total_assets"
    assert deferred.iloc[0].mapping_status == "DEFERRED_CUSTOM_TAG"


def test_wrong_unit_or_basis_never_falls_back(mapping_catalog):
    facts = pd.DataFrame([{"xbrl_namespace": "us-gaap", "xbrl_tag": "Assets", "unit": "shares", "fact_type": "INSTANT", "value": 10.0, "mapping_status": "UNMAPPED"}])
    mapped, deferred = apply_canonical_mapping(facts, mapping_catalog)
    assert mapped.empty
    assert deferred.iloc[0].mapping_status == "DEFERRED_UNIT_OR_BASIS"
```

- [ ] **Step 2: Verify tests fail**

Run:

```bash
PYTHONPATH=data_pipelines/sec_edgar/src:src .venv/bin/pytest -q data_pipelines/sec_edgar/tests/test_mapping.py
```

Expected: import fails for `mapping`.

- [ ] **Step 3: Populate reviewed mapping rows**

The CSV schema is `canonical_field,taxonomy,tag,unit,fact_type,statement,required_basis,priority,effective_from,effective_to,semantic_grade,review_evidence`. Include explicit standard-tag rows for all approved base fields. Primary mappings include `Assets`, `Liabilities`, `StockholdersEquity`, `AssetsCurrent`, `LiabilitiesCurrent`, `CashAndCashEquivalentsAtCarryingValue`, `InventoryNet`, `AccountsReceivableNetCurrent`, `Goodwill`, `ShortTermBorrowings`, `LongTermDebtCurrent`, `LongTermDebtNoncurrent`, `DeferredTaxAssetsNet`, `DeferredTaxLiabilities`, `DeferredTaxAssetsValuationAllowance`, `RevenueFromContractWithCustomerExcludingAssessedTax`, `SalesRevenueNet`, `GrossProfit`, `OperatingIncomeLoss`, `NetIncomeLoss`, `ResearchAndDevelopmentExpense`, `InterestExpenseNonOperating`, `InterestExpense`, `IncomeTaxExpenseBenefit`, `DepreciationDepletionAndAmortization`, `NetCashProvidedByUsedInOperatingActivities`, `PaymentsToAcquirePropertyPlantAndEquipment`, `PaymentsOfDividends`, `IncomeTaxesPaidNet`, `EntityCommonStockSharesOutstanding`, `WeightedAverageNumberOfSharesOutstandingBasic`, `WeightedAverageNumberOfDilutedSharesOutstanding`, `EarningsPerShareBasic`, and `EarningsPerShareDiluted`.

Assign distinct priorities only where two tags are explicitly accepted for one field. Reject overlapping effective ranges at the same priority.

- [ ] **Step 4: Implement exact mapping join**

Join on namespace, tag, normalized unit, fact type, and effective date. Preserve source tag and mapping row ID. Unmatched standard facts become `DEFERRED_SEMANTIC`; custom facts remain `DEFERRED_CUSTOM_TAG`; no name-similarity or statement-position logic is permitted.

- [ ] **Step 5: Run focused tests and catalog audit**

Run:

```bash
PYTHONPATH=data_pipelines/sec_edgar/src:src .venv/bin/pytest -q data_pipelines/sec_edgar/tests/test_mapping.py
PYTHONPATH=data_pipelines/sec_edgar/src:src .venv/bin/python -m sec_edgar_pipeline.mapping --catalog data_pipelines/sec_edgar/mappings/canonical_fields.csv --validate-only
```

Expected: tests pass and the audit reports every approved base field, zero duplicate active mapping keys, and zero overlapping equal-priority ranges.

- [ ] **Step 6: Commit**

```bash
git add data_pipelines/sec_edgar/mappings/canonical_fields.csv data_pipelines/sec_edgar/src/sec_edgar_pipeline/mapping.py data_pipelines/sec_edgar/tests/test_mapping.py
git commit -m "Map reviewed SEC tags to canonical fields"
```

---

### Task 6: Materialize Period Bases and Deterministic Derived Fields

**Files:**
- Create: `data_pipelines/sec_edgar/src/sec_edgar_pipeline/periods.py`
- Create: `data_pipelines/sec_edgar/src/sec_edgar_pipeline/derived.py`
- Create: `data_pipelines/sec_edgar/tests/test_periods.py`
- Create: `data_pipelines/sec_edgar/tests/test_derived.py`

**Interfaces:**

```text
def materialize_period_bases(mapped_versions: pd.DataFrame) -> pd.DataFrame
def select_versions_asof(versions: pd.DataFrame, cutoff: pd.Timestamp, availability_column: str) -> pd.DataFrame
def materialize_derived_fields(period_versions: pd.DataFrame, price_panel: pd.DataFrame | None, definitions: Sequence[Mapping[str, Any]]) -> pd.DataFrame
```

- [ ] **Step 1: Write failing quarter and restatement tests**

```python
def test_discrete_quarters_follow_q1_h1_9m_fy_identity(cumulative_flow_rows):
    result = materialize_period_bases(cumulative_flow_rows)
    values = result.query("reported_basis == 'QUARTERLY_DISCRETE'").sort_values("fiscal_period").value.tolist()
    assert values == [10.0, 15.0, 15.0, 20.0]


def test_restatement_changes_history_only_after_it_is_available(cumulative_flow_rows_with_amendment):
    result = materialize_period_bases(cumulative_flow_rows_with_amendment)
    before = select_versions_asof(result, pd.Timestamp("2025-03-01T21:00:00Z"), "historical_available_at")
    after = select_versions_asof(result, pd.Timestamp("2025-04-01T21:00:00Z"), "historical_available_at")
    assert before.query("fiscal_period == 'Q4'").iloc[0].value == 20.0
    assert after.query("fiscal_period == 'Q4'").iloc[0].value == 22.0
```

Also test rejection for mismatched fiscal starts, units, contexts, tags, and incompatible filing versions; TTM requires four visible discrete quarters; instant facts never enter duration sums.

- [ ] **Step 2: Write failing derived-field tests**

```python
def test_free_cash_flow_requires_both_inputs(derived_fixture):
    rows = derived_fixture[derived_fixture.canonical_field != "capital_expenditure"]
    result = materialize_derived_fields(rows, None, load_definitions())
    assert result.query("canonical_field == 'free_cash_flow'").empty


def test_market_cap_is_proxy_and_uses_visible_raw_close(derived_fixture, raw_close_panel):
    result = materialize_derived_fields(derived_fixture, raw_close_panel, load_definitions())
    row = result.query("canonical_field == 'market_cap_sec_proxy'").iloc[0]
    assert row.semantic_grade == "HYPOTHESIS_PROXY"
    assert row.value == pytest.approx(row.input_shares * row.input_raw_close)
```

- [ ] **Step 3: Verify tests fail**

Run:

```bash
PYTHONPATH=data_pipelines/sec_edgar/src:src .venv/bin/pytest -q data_pipelines/sec_edgar/tests/test_periods.py data_pipelines/sec_edgar/tests/test_derived.py
```

Expected: imports fail for `periods` and `derived`.

- [ ] **Step 4: Implement explicit basis transformations**

Preserve `RAW_REPORTED`; copy instantaneous balance-sheet facts to `INSTANT`; identify complete fiscal-year flows as `ANNUAL`; derive Q1/H1/9M/FY discrete quarters only from compatible versions; build `TTM` from the latest four PIT-visible discrete quarters. Use deterministic precedence by availability time, accession number, mapping priority, and source fact ID. Reject rather than infer when fiscal calendars or contexts conflict.

- [ ] **Step 5: Implement exact derived formulas**

Use these definitions and require every named input:

```text
total_debt = short_term_debt + long_term_debt_current + long_term_debt_noncurrent
working_capital = current_assets - current_liabilities
free_cash_flow = operating_cash_flow - capital_expenditure
ebit = net_income + income_tax_expense + interest_expense
ebitda = ebit + depreciation_and_amortization
roa = net_income_ttm / mean(total_assets_current, total_assets_prior_year)
roe = net_income_ttm / mean(stockholders_equity_current, stockholders_equity_prior_year)
book_value_per_share = stockholders_equity / shares_outstanding_sec
asset_growth = total_assets_current / total_assets_prior_year - 1
revenue_growth = revenue_ttm / revenue_prior_year_ttm - 1
inventory_turnover = (revenue_ttm - gross_profit_ttm) / mean(inventory_current, inventory_prior_year)
market_cap_sec_proxy = shares_outstanding_sec * raw_close
enterprise_value_sec_proxy = market_cap_sec_proxy + total_debt - cash_and_cash_equivalents
```

Normalize cash-flow capital expenditure to a positive outflow through the mapping catalog sign convention. Zero or negative denominators produce missing output with a recorded reason. Every derived row carries input source fact IDs, input availability maximum, definition hash, price snapshot hash when used, and semantic grade.

- [ ] **Step 6: Run focused tests**

Run:

```bash
PYTHONPATH=data_pipelines/sec_edgar/src:src .venv/bin/pytest -q data_pipelines/sec_edgar/tests/test_periods.py data_pipelines/sec_edgar/tests/test_derived.py
```

Expected: all tests pass.

- [ ] **Step 7: Commit**

```bash
git add data_pipelines/sec_edgar/config/derived_fields.json data_pipelines/sec_edgar/src/sec_edgar_pipeline/periods.py data_pipelines/sec_edgar/src/sec_edgar_pipeline/derived.py data_pipelines/sec_edgar/tests
git commit -m "Derive PIT accounting periods and fields"
```

---

### Task 7: Build Daily PIT Panels and the Read-Only Main-Project Adapter

**Files:**
- Create: `data_pipelines/sec_edgar/src/sec_edgar_pipeline/pit.py`
- Create: `data_pipelines/sec_edgar/tests/test_pit.py`
- Create: `src/us_equity_alpha/sec_fundamentals.py`
- Create: `tests/test_sec_fundamentals.py`

**Interfaces:**

```text
def build_daily_pit_panel(versions: pd.DataFrame, sessions: pd.DatetimeIndex, mode: str) -> pd.DataFrame

class SecFundamentalProvider:
    def __init__(self, manifest_path: Path | str)
    @property
    def evidence_hashes(self) -> Mapping[str, str]
    def load_panels(self, bindings: Sequence[tuple[str, str]], start: str, end: str, mode: str = "HISTORICAL_DIAGNOSTIC") -> dict[str, pd.DataFrame]
```

- [ ] **Step 1: Write failing PIT-panel tests**

```python
def test_future_filing_is_absent_before_availability(version_rows, xnys_sessions):
    panel = build_daily_pit_panel(version_rows, xnys_sessions, "HISTORICAL_DIAGNOSTIC")
    before = panel.query("session == '2026-02-02' and canonical_field == 'revenue'")
    after = panel.query("session == '2026-02-04' and canonical_field == 'revenue'")
    assert before.empty
    assert after.iloc[0].value == 125.0


def test_forward_mode_respects_first_seen(version_rows, xnys_sessions):
    historical = build_daily_pit_panel(version_rows, xnys_sessions, "HISTORICAL_DIAGNOSTIC")
    forward = build_daily_pit_panel(version_rows, xnys_sessions, "FORWARD_OBSERVED")
    assert len(historical) > len(forward)
    assert forward.available_at.le(forward.signal_cutoff).all()
```

Test after-close acceptance, acceptance within ten minutes of close, date-only availability, no backward fill, amendment replacement only after availability, daily carry-forward, and daily window length preservation.

- [ ] **Step 2: Write failing provider tests**

```python
def test_provider_rejects_tampered_release(sec_release_fixture):
    manifest = sec_release_fixture / "processed_manifest.json"
    (sec_release_fixture / "canonical_field_catalog.csv").write_text("tampered\n")
    with pytest.raises(ValueError, match="OUTPUT_HASH_MISMATCH"):
        SecFundamentalProvider(manifest)


def test_provider_returns_requested_field_basis_panel(sec_release_fixture):
    provider = SecFundamentalProvider(sec_release_fixture / "processed_manifest.json")
    panels = provider.load_panels([("revenue", "TTM")], "2025-01-01", "2025-03-31")
    assert set(panels) == {"revenue__TTM"}
    assert panels["revenue__TTM"].index.name == "session"
    assert panels["revenue__TTM"].columns.name == "security_id"
```

- [ ] **Step 3: Verify tests fail**

Run:

```bash
PYTHONPATH=data_pipelines/sec_edgar/src:src .venv/bin/pytest -q data_pipelines/sec_edgar/tests/test_pit.py tests/test_sec_fundamentals.py
```

Expected: imports fail for `pit` and `us_equity_alpha.sec_fundamentals`.

- [ ] **Step 4: Implement long-form daily materialization**

Output columns are `session`, `signal_cutoff`, `security_id`, `issuer_cik`, `canonical_field`, `reported_basis`, `value`, `unit`, `selected_available_at`, `historical_available_at`, `first_seen_at`, `accession_number`, `source_fact_id`, `semantic_grade`, `mapping_version`, `definition_hash`, and `availability_mode`. Materialize both approved modes as separate rows. Sort deterministically and partition Parquet by `availability_mode` and `canonical_field`.

- [ ] **Step 5: Implement fail-closed adapter**

The adapter verifies every file hash, requires `validation_status=PASS`, requires a concrete dataset version, validates the requested field/basis against the catalogs, rejects ambiguous identities, reads only required Parquet partitions, pivots to session-by-security panels, and exposes dataset, mapping, derived-catalog, identity-map, PIT-contract, universe, and output hashes. It performs no downloading or accounting interpretation.

- [ ] **Step 6: Run focused tests**

Run:

```bash
PYTHONPATH=data_pipelines/sec_edgar/src:src .venv/bin/pytest -q data_pipelines/sec_edgar/tests/test_pit.py tests/test_sec_fundamentals.py
```

Expected: all tests pass.

- [ ] **Step 7: Commit**

```bash
git add data_pipelines/sec_edgar/src/sec_edgar_pipeline/pit.py data_pipelines/sec_edgar/tests/test_pit.py src/us_equity_alpha/sec_fundamentals.py tests/test_sec_fundamentals.py
git commit -m "Expose hash-verified daily SEC PIT panels"
```

---

### Task 8: Validate, Scale, and Publish `SEC_PIT_FUNDAMENTALS_V1`

**Files:**
- Create: `data_pipelines/sec_edgar/src/sec_edgar_pipeline/validation.py`
- Create: `data_pipelines/sec_edgar/src/sec_edgar_pipeline/release.py`
- Create: `data_pipelines/sec_edgar/scripts/run_sec_validate.py`
- Create: `data_pipelines/sec_edgar/scripts/run_sec_build.py`
- Create: `data_pipelines/sec_edgar/tests/test_validation.py`
- Create: `data_pipelines/sec_edgar/tests/test_release.py`
- Create: `data_pipelines/sec_edgar/tests/fixtures/sec/manual_reconciliation_expected.csv`
- Modify: `.gitignore`

**Interfaces:**

```text
def validate_fixture_contracts(versions: pd.DataFrame, panel: pd.DataFrame) -> dict[str, Any]
def validate_manual_issuers(release_inputs: Mapping[str, Path], issuer_ciks: Sequence[str]) -> tuple[pd.DataFrame, str]
def build_sec_release(inputs: Mapping[str, Path], output_dir: Path | str, pipeline_commit: str) -> dict[str, Any]
```

- [ ] **Step 1: Write failing validation and release tests**

```python
def test_scale_build_requires_fixture_and_manual_pass(tmp_path):
    with pytest.raises(ValueError, match="SEC_SCALE_GATE_NOT_PASSED"):
        build_sec_release({"fixture_status": "PASS", "manual_status": "FAIL"}, tmp_path, "abc123")


def test_release_contains_exact_required_files(valid_release_inputs, tmp_path):
    manifest = build_sec_release(valid_release_inputs, tmp_path, "abc123")
    assert set(manifest["output_files"]) == {
        "fundamental_versions.parquet",
        "daily_pit_fundamental_panel.parquet",
        "canonical_field_catalog.csv",
        "derived_field_definitions.json",
        "cik_security_map.csv",
        "validation_report.md",
        "coverage_report.csv",
        "PIT_DATASET_README.md",
    }
    assert manifest["historical_pit_universe_verified"] is False
    assert manifest["validation_status"] == "PASS"
```

Also test empty/non-empty output rules, manifest self-exclusion from its `output_hashes`, deterministic hashes, source lineage completeness, no secret-like keys, and explicit survivorship/development labels.

- [ ] **Step 2: Verify tests fail**

Run:

```bash
PYTHONPATH=data_pipelines/sec_edgar/src:src .venv/bin/pytest -q data_pipelines/sec_edgar/tests/test_validation.py data_pipelines/sec_edgar/tests/test_release.py
```

Expected: imports fail for `validation` and `release`.

- [ ] **Step 3: Implement deterministic fixture gate**

The gate must cover future exclusion, near-close timing, after-close timing, date-only records, restatement isolation, Q1/H1/9M/FY conversion, incompatible cumulative-period rejection, TTM visibility, instant/duration separation, units/signs, missing-value propagation, identity ambiguity, custom tags, hash determinism, accession lineage, and provider hash rejection. Any failed check sets `validation_status=FAIL` and blocks a scale release.

- [ ] **Step 4: Implement five-issuer manual reconciliation**

Require AAPL `0000320193`, MSFT `0000789019`, JPM `0000019617`, XOM `0000034088`, and WMT `0000104169`. For each, compare selected Assets, Revenue, Net Income, Operating Cash Flow, and Shares facts to the underlying accession/tag/unit; verify one period transformation and one availability transition; record unresolved mappings without filling them. Add deterministic multi-class and amendment fixtures to the same report. The report passes only when every compared populated value has matching raw lineage and timing evidence.

- [ ] **Step 5: Implement immutable release builder**

Write the exact dataset release files in section 18.1 of the approved spec plus `processed_manifest.json`. The manifest contains `dataset_version`, `pipeline_commit`, `created_at`, `universe_snapshot_hash`, `raw_manifest_hash`, `canonical_mapping_hash`, `derived_field_catalog_hash`, `security_mapping_hash`, `pit_contract_hash`, date range, counts, provider, `historical_pit_universe_verified=false`, `survivorship_bias=true`, `selection_evidence=DEVELOPMENT_EXPOSED`, validation status, relative output files, and SHA-256 hashes.

- [ ] **Step 6: Add private storage ignore rules and finite build scripts**

Ensure `.gitignore` covers `private/sec_edgar/`. `run_sec_validate.py` writes fixture and manual reports without changing raw data. `run_sec_build.py` requires explicit universe CSV, raw manifest, price snapshot when proxy market fields are requested, mapping files, policy, validation report, pipeline commit, and empty output directory.

- [ ] **Step 7: Run all automated checks**

Run:

```bash
PYTHONPATH=data_pipelines/sec_edgar/src:src .venv/bin/pytest -q data_pipelines/sec_edgar/tests tests/test_sec_fundamentals.py
PYTHONPATH=src .venv/bin/pytest -q
rg -n -i '(api[_-]?key|secret|password|bearer|authorization)' data_pipelines/sec_edgar src/us_equity_alpha/sec_fundamentals.py tests/test_sec_fundamentals.py
```

Expected: both pytest commands pass. The scan returns only test assertions or documentation that contains no credential value.

- [ ] **Step 8: Run manual gate, then the 500-security build**

Use the frozen development universe:

```bash
PYTHONPATH=data_pipelines/sec_edgar/src:src .venv/bin/python data_pipelines/sec_edgar/scripts/run_sec_download.py --universe-csv private/runs/v5-1-yfinance-500-2019-2026-filter-v2/inputs/universe_500.csv --raw-root private/sec_edgar/raw/SEC_PIT_FUNDAMENTALS_V1 --user-agent-env SEC_USER_AGENT --observed-at "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
PYTHONPATH=data_pipelines/sec_edgar/src:src .venv/bin/python data_pipelines/sec_edgar/scripts/run_sec_validate.py --raw-root private/sec_edgar/raw/SEC_PIT_FUNDAMENTALS_V1 --universe-csv private/runs/v5-1-yfinance-500-2019-2026-filter-v2/inputs/universe_500.csv --output private/sec_edgar/reports/SEC_PIT_FUNDAMENTALS_V1
PYTHONPATH=data_pipelines/sec_edgar/src:src .venv/bin/python data_pipelines/sec_edgar/scripts/run_sec_build.py --raw-root private/sec_edgar/raw/SEC_PIT_FUNDAMENTALS_V1 --universe-csv private/runs/v5-1-yfinance-500-2019-2026-filter-v2/inputs/universe_500.csv --validation-report private/sec_edgar/reports/SEC_PIT_FUNDAMENTALS_V1/validation_report.json --policy data_pipelines/sec_edgar/config/sec_pipeline.json --mapping data_pipelines/sec_edgar/mappings/canonical_fields.csv --derived-definitions data_pipelines/sec_edgar/config/derived_fields.json --output private/sec_edgar/releases/SEC_PIT_FUNDAMENTALS_V1
```

Expected: download completes or resumes with a complete ledger; validation is `PASS`; release manifest verifies; the report states current-universe survivorship bias and makes no Alpha-effectiveness claim. If the manual gate fails, stop with the exact issuer, field, accession, and failed invariant instead of running the scale build.

- [ ] **Step 9: Commit**

```bash
git add .gitignore data_pipelines/sec_edgar src/us_equity_alpha/sec_fundamentals.py tests/test_sec_fundamentals.py
git commit -m "Release SEC PIT fundamentals data product"
```

---

## Plan-Level Acceptance

Run:

```bash
PYTHONPATH=data_pipelines/sec_edgar/src:src .venv/bin/pytest -q data_pipelines/sec_edgar/tests tests/test_sec_fundamentals.py
PYTHONPATH=src .venv/bin/pytest -q
git diff --check
git status --short
```

The plan is complete only when:

- `private/sec_edgar/releases/SEC_PIT_FUNDAMENTALS_V1/processed_manifest.json` verifies every delivered file;
- the fixture and five-issuer validation reports are `PASS`;
- the release contains daily PIT rows with explicit historical and forward availability modes;
- field, identity, period, restatement, source, and raw-object lineage are reversible from every selected value;
- unresolved fields remain missing with explicit reasons;
- no credentials or request headers appear in code, manifests, logs, or delivered artifacts;
- the existing V4 and V5.1 artifact hashes remain unchanged;
- no Alpha reconstruction, filter run, pool change, forward clock, or broker action has occurred.
