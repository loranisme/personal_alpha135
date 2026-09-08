import json

import pandas as pd
import pytest

from us_equity_alpha.classifications import load_classification_inputs
from us_equity_alpha.cli import main
from us_equity_alpha.tiingo_classifications import (
    fetch_current_classifications,
    write_classification_bundle,
)


class Response:
    status_code = 200

    def json(self):
        return [
            {"ticker": "AAPL", "permaTicker": "US0001", "sector": "Technology",
             "industry": "Electronic Equipment", "sicCode": 3571},
            {"ticker": "MSFT", "permaTicker": "US0002", "sicSector": "Services",
             "sicIndustry": "Prepackaged Software", "sicCode": 7372},
        ]


class Session:
    def get(self, url, headers, timeout):
        assert url.endswith("/fundamentals/meta")
        assert headers["Authorization"].startswith("Token ")
        assert timeout == 30
        return Response()


def test_tiingo_metadata_is_explicit_current_only_taxonomy_proxy(tmp_path):
    timestamp = pd.Timestamp("2026-09-08T12:00:00Z")
    frame, evidence = fetch_current_classifications(
        "secret", session=Session(), fetched_at=timestamp
    )
    assert frame.set_index("symbol").loc["AAPL", "subindustry"] == "SIC:3571"
    assert evidence["historical_pit_verified"] is False
    assert evidence["brain_taxonomy_equivalent"]["INDUSTRY"] is False
    assert "secret" not in json.dumps(evidence)
    bundle = tmp_path / "bundle"
    write_classification_bundle(frame, evidence, bundle)
    dates = pd.Index([timestamp.date()])
    close = pd.DataFrame([[1.0, 2.0]], index=dates, columns=["AAPL", "MSFT"])
    panels, capabilities, _ = load_classification_inputs(bundle, {"tiingo": {"close": close}})
    assert panels["tiingo"]["SECTOR"].notna().all().all()
    assert capabilities["INDUSTRY"]["real_classification_verified"] is True
    assert capabilities["INDUSTRY"]["historical_pit_verified"] is False


def test_tiingo_classification_requires_token():
    with pytest.raises(ValueError, match="TIINGO_API_KEY_REQUIRED"):
        fetch_current_classifications("")


def test_fetch_classification_cli_requires_environment_token(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("TIINGO_API_KEY", raising=False)
    assert main(["fetch-tiingo-classifications", "--output", str(tmp_path / "out")]) == 2
    assert json.loads(capsys.readouterr().out)["status"] == "BLOCKED_DATA_AUTH"
