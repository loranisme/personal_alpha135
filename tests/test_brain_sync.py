import json
import os
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
from pathlib import Path

import pytest

from us_equity_alpha.brain_sync import (
    AuthActionRequired,
    AuthPermissionDenied,
    BrainClient,
    BrainSyncError,
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
    evidence = json.loads((tmp_path / "evidence.json").read_text())
    assert evidence["authenticated_contract_verified"] is False
    assert evidence["queried_endpoint"] is None


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


def test_unknown_field_without_catalog_is_not_certification_eligible(tmp_path):
    source = tmp_path / "unknown.json"
    source.write_text(json.dumps({"records": [{"alpha_id": "synthetic", "expression": "rank(unknown_field)"}]}))
    import_alpha_files([source], tmp_path / "out")
    row = json.loads((tmp_path / "out" / "alpha_registry.json").read_text())["records"][0]
    assert row["dependencies"]["safe"] is True
    assert row["dependencies"]["unknown_identifiers"] == ["unknown_field"]
    assert row["certification_eligible"] is False


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


def test_live_regular_object_exposes_code_for_dependency_parsing(tmp_path):
    source = tmp_path / "live-shape.json"
    source.write_text(json.dumps({
        "records": [{
            "id": "synthetic-live-id",
            "regular": {"code": "rank(close)", "operatorCount": 1},
            "settings": {"neutralization": "INDUSTRY"},
        }],
        "sync_scope_complete": True,
        "search_history_complete": False,
    }))
    catalog = tmp_path / "fields.json"
    catalog.write_text(json.dumps({"results": [{"id": "close", "type": "MATRIX"}]}))

    result = import_alpha_files([source], tmp_path / "out", catalog)

    row = json.loads((tmp_path / "out" / "alpha_registry.json").read_text())["records"][0]
    assert result["unique_alpha_count"] == 1
    assert row["dependencies"]["fields"] == [{"identifier": "close", "type": "MATRIX"}]
    assert row["dependencies"]["operators"] == ["rank"]
    assert row["dependencies"]["safe"] is True


def test_brain_comments_lowercase_booleans_and_observed_operators_parse_safely():
    catalog = {"close": {"type": "MATRIX"}, "volume": {"type": "MATRIX"}}
    expression = """/* research note with an apostrophe: stock's */
x = ts_delay(close, 1);
hump(ts_scale(x, 20), hump=0.01) + if_else(volume > 0, sign(x), true)
"""

    report = analyze_expression(expression, catalog)

    assert report["fields"] == [
        {"identifier": "close", "type": "MATRIX"},
        {"identifier": "volume", "type": "MATRIX"},
    ]
    assert report["unknown_identifiers"] == []
    assert report["unknown_operators"] == []
    assert report["safe"] is True


def test_brain_multiline_arithmetic_is_whitespace_not_a_statement_boundary():
    catalog = {"close": {"type": "MATRIX"}, "volume": {"type": "MATRIX"}}
    report = analyze_expression("rank(close)\n  * rank(volume)", catalog)

    assert report["fields"] == [
        {"identifier": "close", "type": "MATRIX"},
        {"identifier": "volume", "type": "MATRIX"},
    ]
    assert report["unsupported_syntax"] == []
    assert report["safe"] is True


def test_brain_multiline_arithmetic_with_trailing_operator_is_parsed():
    catalog = {"close": {"type": "MATRIX"}, "volume": {"type": "MATRIX"}}
    report = analyze_expression("rank(close) *\nrank(volume)", catalog)

    assert [field["identifier"] for field in report["fields"]] == ["close", "volume"]
    assert report["unsupported_syntax"] == []
    assert report["safe"] is True


@pytest.mark.parametrize("name", ["eval", "exec", "open", "__import__"])
def test_python_execution_and_io_calls_are_never_safe(name):
    report = analyze_expression(f"{name}('synthetic')", {})
    assert report["parsed_without_execution"] is True
    assert report["safe"] is False
    assert name in report["unknown_operators"]
    assert "UNSUPPORTED_OPERATOR" in report["unsupported_syntax"]


@pytest.mark.parametrize("expression", ["obj.attr", "x[0]", "__import__('os').system('id')"])
def test_unsafe_expression_syntax_is_flagged(expression):
    report = analyze_expression(expression, {})
    assert report["safe"] is False
    assert report["unsupported_syntax"]


@pytest.mark.parametrize("expression", [
    "while True:\n    pass",
    "for x in close:\n    pass",
    "def factor():\n    return close",
    "lambda x: x",
    "del close",
    "[x for x in close]",
    "{x: x for x in close}",
    "try:\n    rank(close)\nexcept:\n    pass",
])
def test_non_dsl_ast_nodes_are_rejected(expression):
    report = analyze_expression(expression, {"close": {"type": "MATRIX"}})
    assert report["parsed_without_execution"] is True
    assert report["safe"] is False
    assert report["unsupported_syntax"]


def test_keyword_unpacking_is_rejected():
    report = analyze_expression("rank(close, **options)", {"close": {"type": "MATRIX"}})
    assert report["safe"] is False


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


def test_changed_declared_count_is_partial(tmp_path):
    transport = Transport([
        Response(200, {"results": [{"id": "a"}], "count": 3, "next": "next"}),
        Response(200, {"results": [{"id": "b"}], "count": 2, "next": None}),
    ])
    result = BrainClient(transport=transport).sync_library(tmp_path)
    assert result["status"] == "PARTIAL"
    assert result["completion_reason"] == "COUNT_DRIFT"


def test_changed_declared_count_across_resume_is_partial(tmp_path):
    (tmp_path / "raw_page_00001.json").write_text(json.dumps({"results": [{"id": "a"}], "count": 3, "next": "next"}))
    (tmp_path / "sync_manifest.json").write_text(json.dumps({"status": "PARTIAL", "resume_cursor": "next", "pages_completed": 1}))
    transport = Transport([Response(200, {"results": [{"id": "b"}], "count": 2, "next": None})])
    result = BrainClient(transport=transport).sync_library(tmp_path)
    assert result["status"] == "PARTIAL"
    assert result["completion_reason"] == "COUNT_DRIFT"


def test_same_origin_next_url_offset_is_supported(tmp_path):
    next_url = "https://api.worldquantbrain.com/users/self/alphas?limit=1&offset=1"
    transport = Transport([
        Response(200, {"results": [{"id": "a"}], "count": 2, "next": next_url}),
        Response(200, {"results": [{"id": "b"}], "count": 2, "next": None}),
    ])
    result = BrainClient(transport=transport).sync_library(tmp_path)
    assert result["status"] == "COMPLETE"
    assert transport.calls[1][1] == next_url
    assert "params" not in transport.calls[1][2]
    evidence = json.loads((tmp_path / "evidence.json").read_text())
    assert evidence["observed_pagination_scheme"] == "next_url"


def test_same_origin_next_url_preserves_all_query_parameters(tmp_path):
    next_url = "https://api.worldquantbrain.com/users/self/alphas?limit=1&offset=1&order=-dateCreated&scope=mine%20only"
    transport = Transport([
        Response(200, {"results": [{"id": "a"}], "count": 2, "next": next_url}),
        Response(200, {"results": [{"id": "b"}], "count": 2, "next": None}),
    ])
    result = BrainClient(transport=transport).sync_library(tmp_path)
    assert result["status"] == "COMPLETE"
    assert transport.calls[1][1] == next_url


def test_unsupported_pagination_shape_stays_partial(tmp_path):
    transport = Transport([Response(200, {"results": [{"id": "a"}], "count": 2, "next": {"offset": 1}})])
    result = BrainClient(transport=transport).sync_library(tmp_path)
    assert result["status"] == "PARTIAL"
    assert result["completion_reason"] == "UNSUPPORTED_PAGINATION"


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


def test_resume_uses_exact_validated_next_url(tmp_path):
    next_url = "https://api.worldquantbrain.com/users/self/alphas?offset=1&order=-dateCreated&scope=mine%20only"
    (tmp_path / "raw_page_00001.json").write_text(json.dumps({"results": [{"id": "a"}], "count": 2, "next": next_url}))
    (tmp_path / "sync_manifest.json").write_text(json.dumps({"status": "PARTIAL", "resume_cursor": next_url, "pages_completed": 1}))
    transport = Transport([Response(200, {"results": [{"id": "b"}], "count": 2, "next": None})])
    result = BrainClient(transport=transport).sync_library(tmp_path)
    assert result["status"] == "COMPLETE"
    assert transport.calls[0][1] == next_url
    assert "params" not in transport.calls[0][2]


def test_resume_manifest_must_match_last_raw_continuation(tmp_path):
    (tmp_path / "raw_page_00001.json").write_text(json.dumps({"results": [{"id": "a"}], "count": 2, "next": "expected"}))
    (tmp_path / "sync_manifest.json").write_text(json.dumps({"status": "PARTIAL", "resume_cursor": "different", "pages_completed": 1}))
    with pytest.raises(Exception) as error:
        BrainClient(transport=Transport([])).sync_library(tmp_path)
    assert str(error.value) == "INVALID_RESUME_CHAIN"


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
    transport = Transport([
        Response(200, {"authenticated": True, "user": {"id": "synthetic"}}, headers={"Set-Cookie": "session=test; Path=/"}, url="https://api.worldquantbrain.com/authentication"),
        Response(200, {"results": [], "count": 0, "next": None}),
    ])
    client = BrainClient(transport=transport)
    session = tmp_path / "session.json"
    result = login_interactive(client, session, input_fn=lambda _: "user@example.test", getpass_fn=lambda _: "pw")
    assert result["status"] == "AUTHENTICATED"
    assert oct(session.stat().st_mode & 0o777) == "0o600"
    saved = session.read_text()
    assert "pw" not in saved and "user@example.test" not in saved
    assert json.loads(saved)["cookies"] == {"session": "test"}
    assert transport.calls[-1][0] == "GET"


@pytest.mark.parametrize("payload,headers", [
    ({"authenticated": True, "user": {}}, {"Set-Cookie": "session=test"}),
    ({"authenticated": True, "user": {"id": "synthetic"}}, {}),
])
def test_authentication_requires_nonempty_user_and_cookie(tmp_path, payload, headers):
    client = BrainClient(transport=Transport([Response(200, payload, headers=headers)]))
    with pytest.raises(Exception) as error:
        client.authenticate("user@example.test", "pw", tmp_path / "session.json")
    assert str(error.value) in {"AUTH_RESPONSE_INVALID", "AUTH_SESSION_MISSING"}
    assert not (tmp_path / "session.json").exists()


def test_authentication_requires_metadata_capability(tmp_path):
    transport = Transport([
        Response(200, {"authenticated": True, "user": {"id": "synthetic"}}, headers={"Set-Cookie": "session=test"}),
        Response(200, {"detail": "private response must not surface"}),
    ])
    with pytest.raises(Exception) as error:
        BrainClient(transport=transport).authenticate("user@example.test", "pw", tmp_path / "session.json")
    assert str(error.value) == "AUTH_METADATA_INVALID"
    assert not (tmp_path / "session.json").exists()


@pytest.mark.parametrize("status,payload", [
    (200, {"verificationRequired": True}),
    (200, {"actionRequired": True}),
    (401, {"challengeRequired": True}),
    (403, {"verificationRequired": True}),
])
def test_metadata_capability_challenge_requires_user_action(tmp_path, status, payload):
    transport = Transport([
        Response(200, {"authenticated": True, "user": {"id": "synthetic"}}, headers={"Set-Cookie": "session=test"}),
        Response(status, payload),
    ])
    with pytest.raises(AuthActionRequired) as error:
        BrainClient(transport=transport).authenticate("user@example.test", "pw", tmp_path / "session.json")
    assert str(error.value) == "AUTH_ACTION_REQUIRED"
    assert not (tmp_path / "session.json").exists()


def test_plain_initial_401_is_auth_failed(tmp_path):
    client = BrainClient(transport=Transport([Response(401, {})]))
    with pytest.raises(Exception) as error:
        client.authenticate("user@example.test", "pw", tmp_path / "session.json")
    assert not isinstance(error.value, AuthActionRequired)
    assert str(error.value) == "AUTH_FAILED"
    assert not (tmp_path / "session.json").exists()


def test_plain_initial_403_is_auth_failed(tmp_path):
    client = BrainClient(transport=Transport([Response(403, {})]))
    with pytest.raises(Exception) as error:
        client.authenticate("user@example.test", "pw", tmp_path / "session.json")
    assert str(error.value) == "AUTH_FAILED"
    assert not (tmp_path / "session.json").exists()


def test_plain_capability_401_is_auth_failed(tmp_path):
    transport = Transport([
        Response(200, {"authenticated": True, "user": {"id": "synthetic"}}, headers={"Set-Cookie": "session=test"}),
        Response(401, {}),
    ])
    with pytest.raises(Exception) as error:
        BrainClient(transport=transport).authenticate("user@example.test", "pw", tmp_path / "session.json")
    assert not isinstance(error.value, AuthActionRequired)
    assert str(error.value) == "AUTH_FAILED"
    assert not (tmp_path / "session.json").exists()


def test_plain_capability_403_is_permission_denied(tmp_path):
    transport = Transport([
        Response(200, {"authenticated": True, "user": {"id": "synthetic"}}, headers={"Set-Cookie": "session=test"}),
        Response(403, {"detail": "must never appear in error"}),
    ])
    with pytest.raises(AuthPermissionDenied) as error:
        BrainClient(transport=transport).authenticate("user@example.test", "pw", tmp_path / "session.json")
    assert str(error.value) == "AUTH_PERMISSION_DENIED"
    assert "must never appear" not in str(error.value)
    assert not (tmp_path / "session.json").exists()



@pytest.mark.parametrize("status,exception,message", [
    (403, AuthPermissionDenied, "AUTH_PERMISSION_DENIED"),
    (401, BrainSyncError, "AUTH_FAILED"),
    (200, BrainSyncError, "AUTH_METADATA_INVALID"),
])
@pytest.mark.parametrize("body", ["", "<html>private challengeRequired response</html>"])
def test_non_json_capability_preserves_status_without_body_leakage(tmp_path, status, exception, message, body):
    transport = Transport([
        Response(200, {"user": {"id": "synthetic"}}, headers={"Set-Cookie": "session=test"}),
        Response(status, ValueError(body)),
    ])
    with pytest.raises(exception) as error:
        BrainClient(transport=transport).authenticate("user@example.test", "pw", tmp_path / "session.json")
    assert type(error.value) is exception
    assert str(error.value) == message
    assert error.value.__cause__ is None
    assert error.value.__context__ is None
    assert not (tmp_path / "session.json").exists()


def test_retry_after_http_date_is_respected(tmp_path):
    waits = []
    now = datetime(2030, 1, 1, tzinfo=timezone.utc)
    retry_at = format_datetime(now + timedelta(seconds=7), usegmt=True)
    transport = Transport([Response(429, {}, {"Retry-After": retry_at}), Response(200, {"results": [], "count": 0, "next": None})])
    result = BrainClient(transport=transport, sleep=waits.append, now=lambda: now, max_retries=1).sync_library(tmp_path)
    assert result["status"] == "COMPLETE"
    assert waits == [7.0]


def test_malformed_retry_after_becomes_bounded_page_error(tmp_path):
    transport = Transport([Response(429, {}, {"Retry-After": "not-a-date"})])
    with pytest.raises(PageSyncError) as error:
        BrainClient(transport=transport, max_retries=1).sync_library(tmp_path)
    assert str(error.value) == "INVALID_RETRY_AFTER"
    assert json.loads((tmp_path / "sync_manifest.json").read_text())["status"] == "PARTIAL"


def test_import_splits_actual_and_local_explicit_failures(tmp_path):
    source = tmp_path / "failures.json"
    source.write_text(json.dumps({"records": [
        {"alpha_id": "actual", "expression": "rank(close)", "status": "FAIL"},
        {"expression": "rank(close)", "status": "FAIL"},
    ]}))
    summary = import_alpha_files([source], tmp_path / "out")
    assert summary["actual_id_record_count"] == 1
    assert summary["platform_failed_actual_id_record_count"] == 1
    assert summary["local_experiment_count"] == 1
    assert summary["explicit_failed_local_experiment_count"] == 1


def test_cli_import_and_inspect_replace_not_implemented(tmp_path, capsys):
    imported = tmp_path / "imported"
    assert main(["import-brain", "--input", str(FIXTURE), "--output", str(imported)]) == 0
    assert (imported / "platform_metrics.json").exists()
    assert (imported / "dependency_summary.json").exists()
    capsys.readouterr()
    inspected = tmp_path / "inspected"
    assert main(["inspect-library", "--registry", str(imported / "alpha_registry.json"), "--output", str(inspected)]) == 0
    assert (inspected / "dependency_summary.json").exists()


@pytest.mark.parametrize("content", ["null", "[]", "{}", '{"cookie":""}', "not-json"])
def test_cli_sync_rejects_invalid_session_without_network(tmp_path, capsys, content):
    session = tmp_path / "session.json"
    session.write_text(content)
    assert main(["sync-brain", "--session-file", str(session), "--output", str(tmp_path / "out")]) == 2
    payload = json.loads(capsys.readouterr().out)
    assert payload == {"errors": ["INVALID_SESSION"], "status": "AUTH_SESSION_REQUIRED"}
