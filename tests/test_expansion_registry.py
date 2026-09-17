import hashlib
import json
from pathlib import Path

import pytest

from us_equity_alpha.cli import main
from us_equity_alpha.expansion_registry import (
    load_expansion_snapshot,
    register_expansion,
)


def _canonical_hash(value: dict) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()


def _definition(factor_id: str, version: int) -> dict:
    return {
        "factor_id": factor_id,
        "factor_version": version,
        "expression": "rank(ts_delta(close, 5))",
        "economic_hypothesis": "Short-horizon price continuation persists cross-sectionally.",
        "mechanism_tag": "momentum",
        "required_fields": ["volume", "close", "close"],
        "allowed_providers": ["tiingo_eod", "alpaca_sip", "tiingo_eod"],
        "settings": {"delay": 1, "decay": 0, "neutralization": "NONE"},
        "direction": 1,
        "parent_factor_ids": ["local_base_b", "local_base_a", "local_base_a"],
        "generation_method": "HUMAN_AUTHORED",
        "created_at": "2026-09-16T00:00:00Z",
        "supersedes_factor_id": None,
        "compute_status": "REGISTERED",
    }


def test_register_expansion_is_append_only_and_hashed(tmp_path):
    result = register_expansion(_definition("candidate-a", 1), tmp_path)
    stored = json.loads(Path(result["path"]).read_text(encoding="utf-8"))
    declared = stored.pop("content_hash")
    assert result["compute_status"] == "REGISTERED"
    assert declared == _canonical_hash(stored)
    assert stored["required_fields"] == ["close", "volume"]
    assert stored["allowed_providers"] == ["alpaca_sip", "tiingo_eod"]
    assert stored["parent_factor_ids"] == ["local_base_a", "local_base_b"]
    with pytest.raises(FileExistsError, match="EXPANSION_VERSION_EXISTS"):
        register_expansion(_definition("candidate-a", 1), tmp_path)


def test_changed_expression_requires_new_version(tmp_path):
    register_expansion(_definition("candidate-a", 1), tmp_path)
    changed = _definition("candidate-a", 1) | {"expression": "rank(close)"}
    with pytest.raises(FileExistsError, match="EXPANSION_VERSION_EXISTS"):
        register_expansion(changed, tmp_path)
    result = register_expansion(changed | {"factor_version": 2}, tmp_path)
    assert result["factor_version"] == 2


def test_snapshot_rejects_tampered_records(tmp_path):
    result = register_expansion(_definition("candidate-a", 1), tmp_path)
    path = Path(result["path"])
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["expression"] = "-close"
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="EXPANSION_CONTENT_HASH_MISMATCH"):
        load_expansion_snapshot(tmp_path)


def test_registration_rejects_direction_flipping_and_unknown_fields(tmp_path):
    with pytest.raises(ValueError, match="EXPANSION_DIRECTION_MUST_BE_ONE"):
        register_expansion(_definition("candidate-a", 1) | {"direction": -1}, tmp_path)
    with pytest.raises(ValueError, match="EXPANSION_UNKNOWN_FIELDS"):
        register_expansion(_definition("candidate-a", 1) | {"sharpe": 2.0}, tmp_path)


def test_cli_registers_expansion_without_submitting_orders(tmp_path, capsys):
    definition = tmp_path / "definition.json"
    definition.write_text(json.dumps(_definition("candidate-a", 1)), encoding="utf-8")
    code = main(
        [
            "register-expansion",
            "--definition",
            str(definition),
            "--registry",
            str(tmp_path / "registry"),
        ]
    )
    payload = json.loads(capsys.readouterr().out)
    assert code == 0
    assert payload["status"] == "PASS"
    assert payload["live_orders_submitted"] == 0

