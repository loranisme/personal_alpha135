"""Lossless Alpha import and metadata-only dependency/exposure registry."""

from __future__ import annotations

import ast
import csv
import hashlib
import json
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any


ALLOWED_FACTOR_OPERATORS = frozenset({
    "abs", "add", "bucket", "clip", "densify", "divide", "group_backfill",
    "group_mean", "group_neutralize", "group_rank", "group_scale", "group_zscore",
    "if_else", "log", "max", "min", "multiply", "rank", "reverse", "scale",
    "signed_power", "subtract", "trade_when", "ts_arg_max", "ts_arg_min",
    "ts_backfill", "ts_corr", "ts_covariance", "ts_decay_linear", "ts_delta",
    "ts_max", "ts_mean", "ts_min", "ts_product", "ts_rank", "ts_std_dev",
    "ts_sum", "ts_zscore", "vector_neut", "winsorize", "zscore",
})

ALLOWED_DSL_NODES = (
    ast.Module, ast.Expr, ast.Assign, ast.Name, ast.Load, ast.Store, ast.Call,
    ast.Constant, ast.BinOp, ast.UnaryOp, ast.BoolOp, ast.Compare, ast.IfExp,
    ast.List, ast.Tuple, ast.keyword,
    ast.Add, ast.Sub, ast.Mult, ast.Div, ast.FloorDiv, ast.Mod, ast.Pow,
    ast.UAdd, ast.USub, ast.Not, ast.Invert, ast.And, ast.Or,
    ast.Eq, ast.NotEq, ast.Lt, ast.LtE, ast.Gt, ast.GtE,
)

def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _alpha_id(record: Mapping[str, Any]) -> str | None:
    for key in ("alpha_id", "alphaId", "id"):
        value = record.get(key)
        if value not in (None, ""):
            return str(value)
    return None


def _expression(record: Mapping[str, Any]) -> str | None:
    for key in ("expression", "regular", "formula", "code"):
        value = record.get(key)
        if isinstance(value, str) and value.strip():
            return value
    return None


def _settings(record: Mapping[str, Any]) -> Any:
    value = record.get("settings")
    return value if isinstance(value, Mapping) and value else None


def _platform_failed(record: Mapping[str, Any]) -> bool:
    statuses: list[str] = []
    for key in ("status", "result", "state"):
        if record.get(key) is not None:
            statuses.append(str(record[key]).upper())
    for key in ("checks", "platform_checks"):
        checks = record.get(key, [])
        if isinstance(checks, list):
            for check in checks:
                if isinstance(check, Mapping):
                    statuses.extend(str(check.get(k, "")).upper() for k in ("status", "result") if check.get(k) is not None)
    return "FAIL" in statuses or "FAILED" in statuses


class _DependencyVisitor(ast.NodeVisitor):
    def __init__(self) -> None:
        self.called: set[str] = set()
        self.loaded: set[str] = set()
        self.assigned: set[str] = set()
        self.unsupported: set[str] = set()

    def visit_Call(self, node: ast.Call) -> None:
        if isinstance(node.func, ast.Name):
            self.called.add(node.func.id)
        else:
            self.unsupported.add(type(node.func).__name__)
        for arg in node.args:
            self.visit(arg)
        for keyword in node.keywords:
            if keyword.arg is None:
                self.unsupported.add("KeywordUnpacking")
            self.visit(keyword.value)

    def visit_Assign(self, node: ast.Assign) -> None:
        if len(node.targets) != 1 or not isinstance(node.targets[0], ast.Name):
            self.unsupported.add("AssignmentTarget")
        self.generic_visit(node)

    def visit_Name(self, node: ast.Name) -> None:
        if isinstance(node.ctx, ast.Store):
            self.assigned.add(node.id)
        elif isinstance(node.ctx, ast.Load):
            self.loaded.add(node.id)

    def visit_Attribute(self, node: ast.Attribute) -> None:
        self.unsupported.add("Attribute")

    def visit_Subscript(self, node: ast.Subscript) -> None:
        self.unsupported.add("Subscript")

    def visit_Import(self, node: ast.Import) -> None:
        self.unsupported.add("Import")

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        self.unsupported.add("ImportFrom")


def analyze_expression(expression: str | None, field_catalog: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Parse dependencies without evaluating the expression."""
    catalog = field_catalog or {}
    report: dict[str, Any] = {
        "fields": [], "operators": [], "local_variables": [],
        "unknown_identifiers": [], "unknown_operators": [], "unsupported_syntax": [],
        "parsed_without_execution": False, "safe": False,
    }
    if not expression:
        report["unsupported_syntax"] = ["MISSING_EXPRESSION"]
        return report
    try:
        tree = ast.parse(expression, mode="exec")
    except (SyntaxError, ValueError):
        report["unsupported_syntax"] = ["PARSE_ERROR"]
        return report
    visitor = _DependencyVisitor()
    visitor.visit(tree)
    for node in ast.walk(tree):
        if not isinstance(node, ALLOWED_DSL_NODES):
            visitor.unsupported.add(type(node).__name__)
    report["parsed_without_execution"] = True
    keywords = {"True", "False", "None"}
    field_names = sorted(name for name in visitor.loaded if name in catalog)
    locals_used = sorted(name for name in visitor.loaded if name in visitor.assigned)
    unknown = sorted(visitor.loaded - visitor.called - visitor.assigned - set(field_names) - keywords)
    unknown_operators = sorted(visitor.called - ALLOWED_FACTOR_OPERATORS)
    unsupported = set(visitor.unsupported)
    if unknown_operators:
        unsupported.add("UNSUPPORTED_OPERATOR")
    report.update({
        "fields": [{"identifier": name, "type": str(catalog[name].get("type", "UNKNOWN")).upper() if isinstance(catalog[name], Mapping) else "UNKNOWN"} for name in field_names],
        "operators": sorted(visitor.called),
        "unknown_operators": unknown_operators,
        "local_variables": sorted(visitor.assigned | set(locals_used)),
        "unknown_identifiers": unknown,
        "unsupported_syntax": sorted(unsupported),
        "safe": not unsupported,
    })
    return report


def _load_catalog(path: Path | None) -> dict[str, Any]:
    if path is None:
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload, list):
        return {str(row.get("id") or row.get("name")): row for row in payload if isinstance(row, Mapping) and (row.get("id") or row.get("name"))}
    if isinstance(payload, Mapping):
        rows = payload.get("results") or payload.get("fields") or payload.get("dataFields")
        if isinstance(rows, list):
            return {str(row.get("id") or row.get("name")): row for row in rows if isinstance(row, Mapping) and (row.get("id") or row.get("name"))}
        return {str(key): value for key, value in payload.items() if isinstance(value, Mapping)}
    return {}


def _extract_records(payload: Any) -> tuple[list[tuple[str | None, Any]], dict[str, Any]]:
    metadata = {"sync_scope_complete": False, "search_history_complete": False, "source": {"kind": "file"}}
    if isinstance(payload, list):
        return [(None, row) for row in payload], metadata
    if not isinstance(payload, Mapping):
        return [(None, payload)], metadata
    for key in metadata:
        if key in payload:
            metadata[key] = payload[key]
    for container in ("records", "results"):
        if isinstance(payload.get(container), list):
            return [(None, row) for row in payload[container]], metadata
    for container in ("candidates", "factors", "factor_simulation_results"):
        if isinstance(payload.get(container), Mapping):
            return [(str(key), value) for key, value in payload[container].items()], metadata
    # A top-level candidate map is accepted only when all values look record-like.
    if payload and all(isinstance(value, Mapping) for value in payload.values()):
        return [(str(key), value) for key, value in payload.items()], metadata
    return [(None, payload)], metadata


def import_alpha_files(files: Iterable[Path | str], output_dir: Path | str, field_catalog: Path | str | None = None) -> dict[str, Any]:
    """Import every raw record, retaining provenance and incomplete states."""
    destination = Path(output_dir)
    if destination.exists() and any(destination.iterdir()):
        raise FileExistsError("OUTPUT_DIRECTORY_NOT_EMPTY")
    destination.mkdir(parents=True, exist_ok=True)
    catalog = _load_catalog(Path(field_catalog) if field_catalog else None)
    rows: list[dict[str, Any]] = []
    scope_values: list[bool] = []
    history_values: list[bool] = []
    sources: list[Any] = []
    for source_index, source_value in enumerate(files):
        source = Path(source_value)
        source_bytes = source.read_bytes()
        source_hash = hashlib.sha256(source_bytes).hexdigest()
        payload = json.loads(source_bytes)
        extracted, metadata = _extract_records(payload)
        scope_values.append(metadata.get("sync_scope_complete") is True)
        history_values.append(metadata.get("search_history_complete") is True)
        sources.append(metadata.get("source"))
        for record_index, (candidate_key, raw) in enumerate(extracted):
            provenance = {"source_index": source_index, "source_path": str(source.resolve()), "source_sha256": source_hash, "record_index": record_index, "candidate_key": candidate_key}
            raw_mapping = raw if isinstance(raw, Mapping) else {"unparsed_value": raw}
            alpha_id = _alpha_id(raw_mapping)
            identity_provenance = {"source_sha256": source_hash, "record_index": record_index, "candidate_key": candidate_key}
            local_id = None if alpha_id else "local-" + hashlib.sha256(_canonical(identity_provenance).encode()).hexdigest()[:20]
            expression = _expression(raw_mapping)
            rows.append({
                "alpha_id": alpha_id,
                "local_experiment_id": local_id,
                "settings": _settings(raw_mapping),
                "platform_failed": _platform_failed(raw_mapping),
                "definition_hash": hashlib.sha256(_canonical({"expression": expression, "settings": _settings(raw_mapping)}).encode()).hexdigest(),
                "dependencies": analyze_expression(expression, catalog),
                "provenance": provenance,
                "raw": raw,
                "certification_eligible": True,
            })
    definitions: dict[str, set[str]] = defaultdict(set)
    id_counts: Counter[str] = Counter()
    for row in rows:
        if row["alpha_id"]:
            id_counts[row["alpha_id"]] += 1
            definitions[row["alpha_id"]].add(row["definition_hash"])
    conflicts = {alpha_id for alpha_id, hashes in definitions.items() if len(hashes) > 1}
    for row in rows:
        if row["alpha_id"] in conflicts or not row["dependencies"]["safe"] or row["dependencies"]["unknown_identifiers"]:
            row["certification_eligible"] = False
    summary = {
        "status": "LOCAL_SNAPSHOT_PARTIAL" if not scope_values or not all(scope_values) else "COMPLETE",
        "schema_version": 1,
        "record_count": len(rows),
        "actual_id_record_count": sum(row["alpha_id"] is not None for row in rows),
        "unique_alpha_count": len(id_counts),
        "local_experiment_count": sum(row["alpha_id"] is None for row in rows),
        "duplicate_record_count": sum(id_counts.values()) - len(id_counts),
        "platform_failed_actual_id_record_count": sum(row["alpha_id"] is not None and row["platform_failed"] for row in rows),
        "explicit_failed_local_experiment_count": sum(row["alpha_id"] is None and row["platform_failed"] for row in rows),
        "platform_failed_count": sum(row["alpha_id"] is not None and row["platform_failed"] for row in rows),
        "all_raw_explicit_failed_record_count": sum(row["platform_failed"] for row in rows),
        "missing_settings_actual_id_count": sum(row["alpha_id"] is not None and row["settings"] is None for row in rows),
        "definition_conflict_alpha_count": len(conflicts),
        "sync_scope_complete": bool(scope_values) and all(scope_values),
        "search_history_complete": bool(history_values) and all(history_values),
    }
    registry = {"schema_version": 1, "records": rows, "sync_scope_complete": summary["sync_scope_complete"], "search_history_complete": summary["search_history_complete"], "source": sources}
    exposure = {"schema_version": 1, "records": [{"record_ref": row["alpha_id"] or row["local_experiment_id"], "historical_periods": "UNKNOWN", "basis": "NO_EXPLICIT_PERIOD_EVIDENCE"} for row in rows]}
    metrics = {"schema_version": 1, "records": [{"record_index": index, "record_ref": row["alpha_id"] or row["local_experiment_id"], "metrics": row["raw"].get("metrics")} for index, row in enumerate(rows) if isinstance(row["raw"], Mapping) and "metrics" in row["raw"]]}
    dependency_payload = {"schema_version": 1, "records": [{"record_index": index, "record_ref": row["alpha_id"] or row["local_experiment_id"], **row["dependencies"]} for index, row in enumerate(rows)]}
    (destination / "alpha_registry.json").write_text(json.dumps(registry, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (destination / "exposure_ledger.json").write_text(json.dumps(exposure, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (destination / "platform_metrics.json").write_text(json.dumps(metrics, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (destination / "dependency_summary.json").write_text(json.dumps(dependency_payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    with (destination / "field_dependencies.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["record_index", "record_ref", "identifier", "field_type"])
        for record in dependency_payload["records"]:
            for field in record["fields"]:
                writer.writerow([record["record_index"], record["record_ref"], field["identifier"], field["type"]])
    (destination / "completeness_summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    evidence = {
        "schema_version": 1,
        "queried_endpoint": None,
        "public_documentation_status": "UNVERIFIED_IN_THIS_RUN",
        "authenticated_contract_verified": False,
        "observed_pagination_fields": [],
        "observed_pagination_scheme": "not_observed",
        "source_kind": "local_file_import",
        "contains_response_body_or_secrets": False,
    }
    (destination / "evidence.json").write_text(json.dumps(evidence, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return summary


def inspect_registry(registry_path: Path | str, output_dir: Path | str, field_catalog: Path | str | None = None) -> dict[str, Any]:
    destination = Path(output_dir)
    if destination.exists() and any(destination.iterdir()):
        raise FileExistsError("OUTPUT_DIRECTORY_NOT_EMPTY")
    destination.mkdir(parents=True, exist_ok=True)
    catalog = _load_catalog(Path(field_catalog) if field_catalog else None)
    payload = json.loads(Path(registry_path).read_text(encoding="utf-8"))
    dependencies = []
    field_counts: Counter[str] = Counter()
    operator_counts: Counter[str] = Counter()
    for index, row in enumerate(payload.get("records", [])):
        raw = row.get("raw") if isinstance(row, Mapping) else {}
        report = analyze_expression(_expression(raw) if isinstance(raw, Mapping) else None, catalog)
        dependencies.append({"record_index": index, **report})
        field_counts.update(field["identifier"] for field in report["fields"])
        operator_counts.update(report["operators"])
    summary = {"record_count": len(dependencies), "distinct_field_count": len(field_counts), "distinct_operator_count": len(operator_counts)}
    (destination / "dependency_summary.json").write_text(json.dumps({"summary": summary, "records": dependencies}, indent=2) + "\n")
    with (destination / "field_dependencies.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle); writer.writerow(["identifier", "record_count"]); writer.writerows(sorted(field_counts.items()))
    return summary
