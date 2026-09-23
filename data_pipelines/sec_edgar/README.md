# SEC EDGAR PIT data pipeline

This isolated package builds the versioned `SEC_PIT_FUNDAMENTALS_V1` data
product for the Personal Alpha Library. It downloads official SEC Company
Facts and Submissions payloads for the supplied development universe, preserves
their point-in-time availability, and publishes hash-verified canonical data.

The package does not select Alphas, run the V5.1 filter, update the current
Alpha Library pointer, or place orders. Runtime payloads and releases belong
under the repository's ignored `private/sec_edgar/` directory. Request headers,
including the SEC User-Agent, must never enter release artifacts.

The approved frozen policy is in `config/sec_pipeline.json`. Any change to its
timing, taxonomy, scope, or provenance constants is a new reviewed version.
Derived accounting definitions are separately versioned in
`config/derived_fields.json`.

Each new HTTP response records its own verified receipt time. Older raw
ledgers without `receipt_time_verified=true` remain useful for engineering
diagnostics but fail the release gate and must be acquired again before a
`FORWARD_OBSERVED` release. The optional market-cap and enterprise-value
proxy path is not implemented in this version; requesting it fails closed.
The release manifest keeps the legacy `canonical_mapping_hash` for the
published field catalog and separately records the processing mapping-rule,
derived-rule, and process-summary hashes.

XOM changed issuer on 2026-07-01. The reviewed successor bridge is pinned to
the SEC 8-K and to receipt-verified predecessor and successor raw responses in
`config/reviewed_issuer_succession.json`. To admit the successor for its **current
verified security window only**, pass `--succession-predecessor-root` and
`--succession-filing-root` to each of `run_sec_process.py`,
`run_sec_validate.py`, and `run_sec_build.py`. The first root holds the old CIK's
Company Facts and Submissions responses; the second holds the official 8-K
response as a content-addressed `issuer_succession_filing` artifact. Omission
keeps XOM blocked, and changed hashes require a new review. The bridge does not
grant historical XOM security identity before the verified universe window or
waive the frozen five-issuer manual reconciliation gate. The private evidence
roots are ignored by Git and must be preserved with their ledgers for a later
release verification.

The manual gate may normalize the predecessor CIK through
`build_xom_predecessor_manual_versions`. Those rows are labelled
`XOM_PREDECESSOR_ONLY_NOT_RELEASED` and are used only to reconcile the five
reviewed 2025 10-K facts. They are never appended to the released fundamental
versions or daily PIT panel.

JPM's reviewed `RevenuesNetOfInterestExpense` observation is a bank-specific
`HYPOTHESIS_PROXY` for canonical `revenue`. It is available only to explicitly
proxy-bound factors; a strict BRAIN `sales` reconstruction sees it as missing.
The exact JPM 2025 10-K cover share observation is likewise proxy-only and is
admitted by its reviewed source-fact ID. The review file is copied into a scale
release and bound by the release manifest hash.

Run the package tests from the repository root:

```bash
PYTHONPATH=data_pipelines/sec_edgar/src:src .venv/bin/pytest -q data_pipelines/sec_edgar/tests
```
