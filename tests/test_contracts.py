import json

import pytest

from us_equity_alpha.cli import evaluate_environment_checks, main
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


def test_runtime_command_blocks_missing_release(capsys):
    exit_code = main(["signal", "--release", "release-v1", "--mode", "paper"])

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 2
    assert payload["status"] == "BLOCKED_CONFIG"


@pytest.mark.parametrize("root", [[], None, "config", 3])
def test_preflight_blocks_non_object_json_roots(tmp_path, capsys, root):
    config = tmp_path / "config.json"
    config.write_text(json.dumps(root), encoding="utf-8")

    exit_code = main(["preflight", "--config", str(config), "--stage", "metadata"])

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 2
    assert payload["status"] == "BLOCKED_CONFIG"
    assert payload["errors"] == ["CONFIG_ROOT_NOT_OBJECT"]


def test_environment_status_fails_when_a_required_check_is_false():
    checks = {
        "alphalens_spearman_ic": True,
        "vectorbt_holding_simulation": True,
        "parquet_roundtrip": False,
        "xlsx_roundtrip": True,
        "scipy_import": True,
    }

    assert evaluate_environment_checks(checks) == "FAIL"


def test_environment_exception_returns_safe_failure_json(tmp_path, capsys):
    # A directory cannot be used as the JSON output file; this exercises the
    # real filesystem failure boundary without replacing production behavior.
    exit_code = main(["environment-check", "--output", str(tmp_path)])

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 1
    assert payload["status"] == "FAIL"
    assert payload["errors"] == ["ENVIRONMENT_CHECK_ERROR"]
