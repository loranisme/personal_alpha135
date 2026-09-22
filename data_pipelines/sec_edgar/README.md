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

Run the package tests from the repository root:

```bash
PYTHONPATH=data_pipelines/sec_edgar/src:src .venv/bin/pytest -q data_pipelines/sec_edgar/tests
```
