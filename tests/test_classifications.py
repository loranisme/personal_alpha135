import hashlib
import json

import pandas as pd
import pytest

from us_equity_alpha.classifications import load_classification_inputs


def write_bundle(path, rows, *, levels=("SECTOR", "INDUSTRY", "SUBINDUSTRY")):
    path.mkdir()
    data = path / "classifications.parquet"
    pd.DataFrame(rows).to_parquet(data, index=False)
    report = path / "report.json"
    report.write_text(json.dumps({
        "classification_evidence_verified": True,
        "real_classification_verified": False,
        "historical_pit_verified": True,
        "provider": "synthetic_fixture",
        "levels": list(levels),
        "taxonomy_by_level": {level: "TEST" for level in levels},
        "brain_taxonomy_equivalent": {level: False for level in levels},
    }))
    manifest = {
        item.name: hashlib.sha256(item.read_bytes()).hexdigest()
        for item in (report, data)
    }
    (path / "manifest.json").write_text(json.dumps(manifest))


def test_point_in_time_classifications_do_not_backfill_future_labels(tmp_path):
    rows = [
        {"symbol": "A", "effective_at": "2024-01-01T00:00:00Z", "available_at": "2024-01-01T00:00:00Z",
         "sector": "S1", "industry": "I1", "subindustry": "SI1"},
        {"symbol": "A", "effective_at": "2024-01-03T00:00:00Z", "available_at": "2024-01-04T00:00:00Z",
         "sector": "S2", "industry": "I2", "subindustry": "SI2"},
        {"symbol": "B", "effective_at": "2024-01-01T00:00:00Z", "available_at": "2024-01-01T00:00:00Z",
         "sector": "S1", "industry": "I1", "subindustry": "SI1"},
    ]
    bundle = tmp_path / "bundle"
    write_bundle(bundle, rows)
    dates = pd.Index(pd.to_datetime(["2024-01-02", "2024-01-03", "2024-01-04"], utc=True).date)
    close = pd.DataFrame([[1, 2], [2, 3], [3, 4]], index=dates, columns=["A", "B"])
    panels, capabilities, evidence = load_classification_inputs(
        bundle, {"bars": {"close": close}}
    )
    industry = panels["bars"]["INDUSTRY"]
    assert industry.loc[dates[1], "A"] == "I1"
    assert industry.loc[dates[2], "A"] == "I2"
    assert capabilities["SUBINDUSTRY"]["brain_taxonomy_equivalent"] is False
    assert evidence["report"]["provider"] == "synthetic_fixture"


def test_classification_bundle_hash_is_enforced(tmp_path):
    bundle = tmp_path / "bundle"
    write_bundle(bundle, [{
        "symbol": "A", "effective_at": "2024-01-01T00:00:00Z", "available_at": "2024-01-01T00:00:00Z",
        "sector": "S", "industry": "I", "subindustry": "SI",
    }])
    (bundle / "classifications.parquet").write_bytes(b"tampered")
    close = pd.DataFrame({"A": [1.0]}, index=[pd.Timestamp("2024-01-02").date()])
    with pytest.raises(ValueError, match="CLASSIFICATION_HASH_MISMATCH"):
        load_classification_inputs(bundle, {"bars": {"close": close}})
