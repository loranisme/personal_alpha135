import json
import os
from pathlib import Path

import pytest

from us_equity_alpha.brain_sync import (
    AuthActionRequired,
    BrainClient,
    PageSyncError,
    login_interactive,
)
from us_equity_alpha.cli import main
from us_equity_alpha.registry import analyze_expression, import_alpha_files


FIXTURE = Path(__file__).parent / "fixtures" / "brain_export.json"


class Response:
    def __init__(self, status, payload=None, headers=None, url="https://api.worldquantbrain.com/users/self/alphas"):
        self.status_code = status
        self._payload = payload
        self.headers = headers or {}
        self.url = url

    def json(self):
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


class Transport:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        return self.responses.pop(0)


def test_failed_alpha_is_kept_and_history_is_not_invented(tmp_path):
    result = import_alpha_files([FIXTURE], tmp_path)
    assert result["record_count"] == 3
    assert result["unique_alpha_count"] == 2
    assert result["duplicate_record_count"] == 1
    assert result["platform_failed_count"] == 1
    assert result["missing_settings_actual_id_count"] == 1
    assert result["search_history_complete"] is False
    registry = json.loads((tmp_path / "alpha_registry.json").read_text())
    assert len(registry["records"]) == 3


def test_dictionary_export_keeps_candidate_provenance_and_local_identity(tmp_path):
    source = tmp_path / "input.json"
    source.write_text(json.dumps({"candidates": {"local-one": {"expression": "rank(close)"}}}))
    result = import_alpha_files([source], tmp_path / "out")
    assert result["record_count"] == 1
    assert result["unique_alpha_count"] == 0
    assert result["local_experiment_count"] == 1
    row = json.loads((tmp_path / "out" / "alpha_registry.json").read_text())["records"][0]
    assert row["provenance"]["candidate_key"] == "local-one"
    assert row["local_experiment_id"].startswith("local-")
    assert row["raw"]["expression"] == "rank(close)"


def test_local_identity_is_stable_when_input_order_changes(tmp_path):
    first = tmp_path / "first.json"
    second = tmp_path / "second.json"
    first.write_text(json.dumps({"candidates": {"one": {"expression": "rank(close)"}}}))
    second.write_text(json.dumps({"candidates": {"two": {"expression": "rank(volume)"}}}))
    import_alpha_files([first, second], tmp_path / "ordered")
    import_alpha_files([second, first], tmp_path / "reversed")
    ordered = json.loads((tmp_path / "ordered" / "alpha_registry.json").read_text())["records"]
    reversed_rows = json.loads((tmp_path / "reversed" / "alpha_registry.json").read_text())["records"]
    by_key_ordered = {row["provenance"]["candidate_key"]: row["local_experiment_id"] for row in ordered}
    by_key_reversed = {row["provenance"]["candidate_key"]: row["local_experiment_id"] for row in reversed_rows}
    assert by_key_ordered == by_key_reversed


def test_conflicting_variants_remain_visible_and_uncertified(tmp_path):
    source = tmp_path / "variants.json"
    source.write_text(json.dumps({"records": [
        {"alpha_id": "same", "expression": "rank(close)"},
        {"alpha_id": "same", "expression": "rank(volume)"},
    ]}))
    result = import_alpha_files([source], tmp_path / "out")
    assert result["unique_alpha_count"] == 1
    assert result["definition_conflict_alpha_count"] == 1
    rows = json.loads((tmp_path / "out" / "alpha_registry.json").read_text())["records"]
    assert len(rows) == 2 and all(row["certification_eligible"] is False for row in rows)


def test_expression_dependencies_are_parsed_without_execution():
    catalog = {"close": {"type": "MATRIX"}, "industry": {"type": "GROUP"}}
    report = analyze_expression("x = ts_delta(close, 5); group_rank(x, industry)", catalog)
    assert report["fields"] == [
        {"identifier": "close", "type": "MATRIX"},
        {"identifier": "industry", "type": "GROUP"},
    ]
    assert report["operators"] == ["group_rank", "ts_delta"]
    assert report["local_variables"] == ["x"]
    assert report["unknown_identifiers"] == []
    assert report["safe"] is True


@pytest.mark.parametrize("expression", ["obj.attr", "x[0]", "__import__('os').system('id')"])
def test_unsafe_expression_syntax_is_flagged(expression):
    report = analyze_expression(expression, {})
    assert report["safe"] is False
    assert report["unsupported_syntax"]


def test_pagination_deduplicates_overlap_and_recovers_from_rate_limit(tmp_path):
    transport = Transport([
        Response(200, {"results": [{"id": "a"}, {"id": "b"}], "count": 3, "next": "cursor-2"}),
        Response(429, {}, {"Retry-After": "0"}),
        Response(200, {"results": [{"id": "b"}, {"id": "c"}], "count": 3, "next": None}),
    ])
    client = BrainClient(transport=transport, sleep=lambda _: None, max_retries=1)
    result = client.sync_library(tmp_path, page_size=2)
    assert result["unique_alpha_count"] == 3
    assert result["duplicate_record_count"] == 1
    assert result["sync_scope_complete"] is True
    assert len(transport.calls) == 3


def test_unknown_total_never_claims_complete(tmp_path):
    transport = Transport([Response(200, {"results": [{"id": "a"}], "next": None})])
    result = BrainClient(transport=transport).sync_library(tmp_path)
    assert result["sync_scope_complete"] is False
    assert result["status"] == "PARTIAL"


def test_page_failure_writes_resumable_partial_manifest(tmp_path):
    transport = Transport([
        Response(200, {"results": [{"id": "a"}], "count": 2, "next": "cursor-2"}),
        Response(503, {}), Response(503, {}),
    ])
    client = BrainClient(transport=transport, sleep=lambda _: None, max_retries=1)
    with pytest.raises(PageSyncError):
        client.sync_library(tmp_path)
    manifest = json.loads((tmp_path / "sync_manifest.json").read_text())
    assert manifest["status"] == "PARTIAL"
    assert manifest["sync_scope_complete"] is False
    assert manifest["resume_cursor"] == "cursor-2"


def test_partial_manifest_resumes_without_refetching_completed_page(tmp_path):
    (tmp_path / "raw_page_00001.json").write_text(json.dumps({"results": [{"id": "a"}], "count": 2, "next": "cursor-2"}))
    (tmp_path / "sync_manifest.json").write_text(json.dumps({"status": "PARTIAL", "resume_cursor": "cursor-2", "pages_completed": 1}))
    transport = Transport([Response(200, {"results": [{"id": "b"}], "count": 2, "next": None})])
    result = BrainClient(transport=transport).sync_library(tmp_path)
    assert result["status"] == "COMPLETE"
    assert result["unique_alpha_count"] == 2
    assert transport.calls[0][2]["params"]["cursor"] == "cursor-2"


def test_repeated_cursor_is_partial(tmp_path):
    transport = Transport([Response(200, {"results": [{"id": "a"}], "count": 2, "next": "same"}), Response(200, {"results": [{"id": "b"}], "count": 2, "next": "same"})])
    result = BrainClient(transport=transport).sync_library(tmp_path)
    assert result["status"] == "PARTIAL"
    assert result["completion_reason"] == "REPEATED_CURSOR"


def test_authentication_action_required_and_credentials_not_logged(tmp_path, caplog):
    secret = "synthetic-secret-value"
    transport = Transport([Response(200, {"verificationRequired": True}, url="https://api.worldquantbrain.com/authentication")])
    client = BrainClient(transport=transport)
    with pytest.raises(AuthActionRequired):
        client.authenticate("user@example.test", secret, tmp_path / "session.json")
    assert secret not in caplog.text
    assert not (tmp_path / "session.json").exists()


def test_login_uses_injected_hidden_input_and_mode_0600(tmp_path):
    transport = Transport([Response(200, {"authenticated": True, "user": {"id": "synthetic"}}, headers={"Set-Cookie": "session=test; Path=/"}, url="https://api.worldquantbrain.com/authentication")])
    client = BrainClient(transport=transport)
    session = tmp_path / "session.json"
    result = login_interactive(client, session, input_fn=lambda _: "user@example.test", getpass_fn=lambda _: "pw")
    assert result["status"] == "AUTHENTICATED"
    assert oct(session.stat().st_mode & 0o777) == "0o600"
    saved = session.read_text()
    assert "pw" not in saved and "user@example.test" not in saved


def test_cli_import_and_inspect_replace_not_implemented(tmp_path, capsys):
    imported = tmp_path / "imported"
    assert main(["import-brain", "--input", str(FIXTURE), "--output", str(imported)]) == 0
    assert (imported / "platform_metrics.json").exists()
    assert (imported / "dependency_summary.json").exists()
    capsys.readouterr()
    inspected = tmp_path / "inspected"
    assert main(["inspect-library", "--registry", str(imported / "alpha_registry.json"), "--output", str(inspected)]) == 0
    assert (inspected / "dependency_summary.json").exists()
