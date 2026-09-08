import hashlib
import json

import pandas as pd

from us_equity_alpha.cli import main
from us_equity_alpha.proxy_pipeline import load_provider_inputs, run_proxy_pipeline


def write_run(path, *, include_vwap, mode):
    path.mkdir()
    manifest = {}
    for symbol, base in (("AAPL", 100), ("MSFT", 50)):
        rows = []
        for day in range(1, 9):
            row = {"date": f"2024-01-{day + 1:02}", "open": base + day - 0.5,
                   "high": base + day + 1, "low": base + day - 1,
                   "close": base + day, "volume": 1000 + day}
            if include_vwap:
                row["vwap"] = base + day - 0.1
            rows.append(row)
        file = path / f"raw_{symbol}.json"
        file.write_text(json.dumps(rows))
        manifest[file.name] = hashlib.sha256(file.read_bytes()).hexdigest()
    report = path / "report.json"
    report.write_text(json.dumps({
        "status": "REAL_DATA_LOCAL_DIAGNOSTIC_PASS",
        "mode": mode,
        "feed": "sip" if mode == "alpaca" else None,
        "real_market_data_verified": True,
        "historical_pit_verified": False,
    }))
    manifest[report.name] = hashlib.sha256(report.read_bytes()).hexdigest()
    (path / "manifest.json").write_text(json.dumps(manifest))


def write_classification_run(path):
    path.mkdir()
    data = path / "classifications.parquet"
    pd.DataFrame([
        {"symbol": symbol, "effective_at": "2020-01-01T00:00:00Z",
         "available_at": "2020-01-01T00:00:00Z", "sector": "S",
         "industry": "I", "subindustry": "SI"}
        for symbol in ("AAPL", "MSFT")
    ]).to_parquet(data, index=False)
    report = path / "report.json"
    report.write_text(json.dumps({
        "classification_evidence_verified": True,
        "real_classification_verified": False,
        "historical_pit_verified": True,
        "provider": "fixture",
        "levels": ["SECTOR", "INDUSTRY", "SUBINDUSTRY"],
        "taxonomy_by_level": {"SECTOR": "T", "INDUSTRY": "T", "SUBINDUSTRY": "T"},
        "brain_taxonomy_equivalent": {
            "SECTOR": False, "INDUSTRY": False, "SUBINDUSTRY": False,
        },
    }))
    manifest = {
        item.name: hashlib.sha256(item.read_bytes()).hexdigest() for item in (data, report)
    }
    (path / "manifest.json").write_text(json.dumps(manifest))


def source(alpha_id, expression, fields):
    return {
        "alpha_id": alpha_id,
        "definition_hash": f"hash-{alpha_id}",
        "settings": {"delay": 1, "decay": 0, "neutralization": "NONE"},
        "dependencies": {"safe": True,
                         "fields": [{"identifier": field, "type": "MATRIX"} for field in fields],
                         "operators": ["rank"], "unknown_identifiers": [],
                         "unsupported_syntax": []},
        "raw": {"regular": {"code": expression, "description": None},
                "is": {"sharpe": 5}},
        "provenance": {"source_path": "private/source.json"},
    }


def complete_records(first=()):
    records = list(first)
    existing = {row["alpha_id"] for row in records}
    index = 0
    while len(records) < 886:
        alpha_id = f"PAD{index:04d}"
        index += 1
        if alpha_id not in existing:
            records.append(source(alpha_id, "rank(close)", ["close"]))
    return records


def test_load_provider_inputs_verifies_manifest_and_does_not_invent_vwap(tmp_path):
    alpaca = tmp_path / "alpaca"
    tiingo = tmp_path / "tiingo"
    write_run(alpaca, include_vwap=True, mode="alpaca")
    write_run(tiingo, include_vwap=False, mode="tiingo")
    inputs = load_provider_inputs({"alpaca_sip": alpaca, "tiingo_eod": tiingo})
    assert set(inputs["alpaca_sip"]) == {"open", "high", "low", "close", "volume", "vwap"}
    assert set(inputs["tiingo_eod"]) == {"open", "high", "low", "close", "volume"}
    assert inputs["alpaca_sip"]["close"].shape == (8, 2)


def test_pipeline_gives_every_source_one_decision_and_writes_library(tmp_path):
    registry = tmp_path / "registry.json"
    mapping = tmp_path / "mapping.json"
    registry.write_text(json.dumps({"sync_scope_complete": True, "records": complete_records([
        source("A", "rank(-returns)", ["returns"]),
        source("F", "rank(cashflow_op)", ["cashflow_op"]),
    ])}))
    mapping.write_text(json.dumps([
        {"field_id": "returns", "dataset": "pv1", "description": "Daily return"},
        {"field_id": "cashflow_op", "dataset": "fundamental2", "description": "Operating cash flow"},
    ]))
    alpaca = tmp_path / "alpaca"
    tiingo = tmp_path / "tiingo"
    write_run(alpaca, include_vwap=True, mode="alpaca")
    write_run(tiingo, include_vwap=False, mode="tiingo")
    output = tmp_path / "output"
    report = run_proxy_pipeline(registry, mapping,
                                {"alpaca_sip": alpaca, "tiingo_eod": tiingo}, output)
    assert report["source_count"] == 886
    assert report["admitted_source_count"] == 885
    assert report["deferred_source_count"] == 1
    assert report["compute_verified_count"] == 2
    assert report["input_evidence"]["registry_sha256"] == hashlib.sha256(registry.read_bytes()).hexdigest()
    assert set(report["input_evidence"]["provider_manifest_sha256"]) == {"alpaca_sip", "tiingo_eod"}
    assert (output / "project_factor_library.json").exists()
    decisions = [json.loads(line) for line in (output / "proxy_decisions.jsonl").read_text().splitlines()]
    assert len({row["source_alpha_id"] for row in decisions}) == 886
    assert {"A", "F"}.issubset({row["source_alpha_id"] for row in decisions})


def test_pipeline_admits_settings_neutralization_only_with_classification_bundle(tmp_path):
    registry = tmp_path / "registry.json"
    neutralized = source("N", "rank(close)", ["close"])
    neutralized["settings"]["neutralization"] = "INDUSTRY"
    registry.write_text(json.dumps({
        "sync_scope_complete": True, "records": complete_records([neutralized])
    }))
    mapping = tmp_path / "mapping.json"
    mapping.write_text(json.dumps([{"field_id": "close", "dataset": "pv1"}]))
    bars = tmp_path / "bars"
    write_run(bars, include_vwap=True, mode="alpaca")
    without = run_proxy_pipeline(
        registry, mapping, {"alpaca_sip": bars}, tmp_path / "without"
    )
    assert without["deferred_source_count"] == 1
    classifications = tmp_path / "classifications"
    write_classification_run(classifications)
    with_groups = run_proxy_pipeline(
        registry, mapping, {"alpaca_sip": bars}, tmp_path / "with", classifications
    )
    assert with_groups["deferred_source_count"] == 0
    assert with_groups["compute_verified_source_count"] == 886
    assert with_groups["input_evidence"]["classification_evidence"]["report"][
        "real_classification_verified"
    ] is False


def test_convert_library_cli_runs_the_real_pipeline(tmp_path, capsys):
    registry = tmp_path / "registry.json"
    mapping = tmp_path / "mapping.json"
    registry.write_text(json.dumps({"sync_scope_complete": True,
                                    "records": complete_records([
                                        source("A", "rank(-returns)", ["returns"])
                                    ])}))
    mapping.write_text(json.dumps([
        {"field_id": "returns", "dataset": "pv1", "description": "Daily return"}
    ]))
    alpaca = tmp_path / "alpaca"
    tiingo = tmp_path / "tiingo"
    write_run(alpaca, include_vwap=True, mode="alpaca")
    write_run(tiingo, include_vwap=False, mode="tiingo")
    output = tmp_path / "output"
    exit_code = main([
        "convert-library", "--registry", str(registry), "--field-mapping", str(mapping),
        "--provider-run", f"alpaca_sip={alpaca}", "--provider-run", f"tiingo_eod={tiingo}",
        "--output", str(output),
    ])
    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert payload["source_count"] == 886
    assert payload["compute_verified_count"] == 2


def test_unlisted_raw_file_is_rejected_even_when_other_hashes_match(tmp_path):
    run = tmp_path / "run"
    write_run(run, include_vwap=True, mode="alpaca")
    (run / "raw_UNLISTED.json").write_text((run / "raw_AAPL.json").read_text())
    import pytest
    with pytest.raises(ValueError, match="SOURCE_RAW_NOT_IN_MANIFEST"):
        load_provider_inputs({"alpaca_sip": run})


def test_pipeline_rejects_incomplete_duplicate_or_wrong_sized_registry(tmp_path):
    import pytest
    mapping = tmp_path / "mapping.json"
    mapping.write_text(json.dumps([{"field_id": "close", "dataset": "pv1"}]))
    alpaca = tmp_path / "alpaca"
    write_run(alpaca, include_vwap=True, mode="alpaca")
    for name, payload, error in (
        ("incomplete", {"sync_scope_complete": False, "records": [source("A", "rank(close)", ["close"])]},
         "REGISTRY_SYNC_SCOPE_INCOMPLETE"),
        ("duplicate", {"sync_scope_complete": True, "records": [source("A", "rank(close)", ["close"]), source("A", "rank(close)", ["close"])]},
         "REGISTRY_ALPHA_ID_DUPLICATE"),
        ("short", {"sync_scope_complete": True, "records": [source("A", "rank(close)", ["close"])]},
         "REGISTRY_SOURCE_COUNT_MISMATCH"),
    ):
        registry = tmp_path / f"{name}.json"
        registry.write_text(json.dumps(payload))
        with pytest.raises(ValueError, match=error):
            run_proxy_pipeline(registry, mapping, {"alpaca_sip": alpaca}, tmp_path / f"out-{name}")


def test_provider_identity_and_dynamic_coverage_are_evidence_backed(tmp_path):
    import pytest
    registry = tmp_path / "registry.json"
    mapping = tmp_path / "mapping.json"
    registry.write_text(json.dumps({"sync_scope_complete": True,
                                    "records": complete_records([
                                        source("A", "rank(close)", ["close"])
                                    ])}))
    mapping.write_text(json.dumps([{"field_id": "close", "dataset": "pv1"}]))
    alpaca = tmp_path / "alpaca"
    write_run(alpaca, include_vwap=True, mode="alpaca")
    with pytest.raises(ValueError, match="PROVIDER_EVIDENCE_MISMATCH"):
        run_proxy_pipeline(registry, mapping, {"tiingo_eod": alpaca}, tmp_path / "wrong",
                           )
    output = tmp_path / "right"
    run_proxy_pipeline(registry, mapping, {"alpaca_sip": alpaca}, output)
    capabilities = json.loads((output / "data_capabilities.json").read_text())
    scope = capabilities["close"]["coverage_scope"]["alpaca_sip"]
    assert scope["symbols"] == ["AAPL", "MSFT"]
    assert scope["start_date"] == "2024-01-02"
    assert scope["end_date"] == "2024-01-09"
