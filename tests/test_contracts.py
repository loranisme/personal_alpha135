import json

from us_equity_alpha.cli import main
from us_equity_alpha.contracts import validate_config


def test_missing_release_config_blocks():
    errors = validate_config({}, stage="release")
    assert "BLOCKED_CONFIG" in errors
    assert "MISSING_N" in errors
    assert "MISSING_EVALUATION_PROTOCOL" in errors


def test_metadata_stage_does_not_require_release_settings():
    assert validate_config({}, stage="metadata") == []


def test_null_release_value_is_unresolved():
    errors = validate_config({"N": None}, stage="release")
    assert "BLOCKED_CONFIG" in errors
    assert "MISSING_N" in errors


def test_unknown_stage_is_rejected():
    assert validate_config({}, stage="unknown") == ["UNKNOWN_STAGE"]


def test_preflight_reports_blocking_codes(tmp_path, capsys):
    config = tmp_path / "config.json"
    config.write_text("{}", encoding="utf-8")

    exit_code = main(["preflight", "--config", str(config), "--stage", "release"])

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 2
    assert payload["status"] == "BLOCKED_CONFIG"
    assert "MISSING_N" in payload["errors"]


def test_future_command_is_explicitly_not_implemented(capsys):
    exit_code = main(["signal", "--release", "release-v1", "--mode", "paper"])

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 3
    assert payload == {"command": "signal", "status": "NOT_IMPLEMENTED"}
