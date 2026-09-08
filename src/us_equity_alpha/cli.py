"""Command-line contracts for phase-one engineering."""

from __future__ import annotations

import argparse
import importlib.metadata
import json
import math
import os
import platform
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Sequence

from .contracts import STAGE_REQUIRED_FIELDS, validate_config


FUTURE_COMMANDS = {
    "validate": (("--stage",), ("--protocol",)),
    "freeze": (("--stage",), ("--config",)),
    "signal": (("--release",), ("--mode",)),
    "execution-preview": (
        ("--signal-json",), ("--quotes",), ("--positions",), ("--account",)
    ),
    "reconcile": (("--fills",), ("--positions",), ("--account",)),
}


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="us-equity-alpha",
        description="Local phase-one contracts; never submits orders or Alphas.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    preflight = subparsers.add_parser(
        "preflight", help="Read a JSON config and report stage blockers."
    )
    preflight.add_argument("--config", required=True, type=Path)
    preflight.add_argument(
        "--stage", choices=tuple(STAGE_REQUIRED_FIELDS), default="metadata"
    )

    environment = subparsers.add_parser(
        "environment-check",
        help="Run local synthetic dependency and file-format compatibility checks.",
    )
    environment.add_argument("--output", required=True, type=Path)

    for name, options in FUTURE_COMMANDS.items():
        command = subparsers.add_parser(
            name, help="Reserved command; currently returns NOT_IMPLEMENTED."
        )
        for option in options:
            command.add_argument(*option, required=True)
    importer = subparsers.add_parser("import-brain", help="Import local BRAIN exports losslessly.")
    importer.add_argument("--input", required=True, action="append", type=Path)
    importer.add_argument("--output", required=True, type=Path)
    importer.add_argument("--field-catalog", type=Path)
    inspector = subparsers.add_parser("inspect-library", help="Build expression dependency metadata.")
    inspector.add_argument("--registry", required=True, type=Path)
    inspector.add_argument("--field-catalog", type=Path)
    inspector.add_argument("--output", required=True, type=Path)
    login = subparsers.add_parser("login-brain", help="Authenticate interactively without echoing credentials.")
    login.add_argument("--session-file", required=True, type=Path)
    sync = subparsers.add_parser("sync-brain", help="Synchronize allowlisted read-only Alpha metadata.")
    sync.add_argument("--session-file", required=True, type=Path)
    sync.add_argument("--output", required=True, type=Path)
    sync.add_argument("--max-pages", type=int)
    sync.add_argument("--page-size", type=int, default=100)
    mapper = subparsers.add_parser("map-library", help="Map BRAIN dependencies to documented provider candidates.")
    mapper.add_argument("--registry", required=True, type=Path)
    mapper.add_argument("--field-catalog", required=True, type=Path)
    mapper.add_argument("--output", required=True, type=Path)
    converter = subparsers.add_parser(
        "convert-library",
        help="Build a performance-blind direct/proxy candidate factor library.",
    )
    converter.add_argument("--registry", required=True, type=Path)
    converter.add_argument("--field-mapping", required=True, type=Path)
    converter.add_argument(
        "--provider-run", required=True, action="append",
        help="Saved provider snapshot as NAME=PATH; may be repeated.",
    )
    converter.add_argument("--output", required=True, type=Path)
    converter.add_argument("--classification-run", type=Path)
    probe = subparsers.add_parser("probe-data", help="Run one finite authenticated provider sample.")
    probe.add_argument("--provider", required=True, choices=("alpaca", "tiingo"))
    probe.add_argument("--output", required=True, type=Path)
    classifications = subparsers.add_parser(
        "fetch-tiingo-classifications",
        help="Fetch current Tiingo Fundamentals classifications into a hashed bundle.",
    )
    classifications.add_argument("--output", required=True, type=Path)
    return parser


def _emit(payload: dict) -> None:
    print(json.dumps(payload, indent=2, sort_keys=True))


def evaluate_environment_checks(checks: Mapping[str, bool]) -> str:
    """Return PASS only when every required compatibility assertion passes."""
    return "PASS" if checks and all(checks.values()) else "FAIL"


def _environment_check(output: Path) -> dict:
    import numpy as np
    import pandas as pd
    import scipy
    import vectorbt as vbt
    from alphalens.performance import factor_information_coefficient

    dates = pd.date_range("2024-01-02", periods=4, tz="UTC")
    assets = ["A", "B", "C"]
    index = pd.MultiIndex.from_product([dates, assets], names=["date", "asset"])
    factors = np.tile([1.0, 2.0, 3.0], len(dates))
    forward = np.tile([0.01, 0.02, 0.03], len(dates))
    factor_data = pd.DataFrame({"factor": factors, "1D": forward}, index=index)
    ic = factor_information_coefficient(factor_data)["1D"]

    prices = pd.Series([100.0, 101.0, 99.0, 102.0], index=dates)
    entries = pd.Series([True, False, False, False], index=dates)
    exits = pd.Series([False, False, False, True], index=dates)
    portfolio = vbt.Portfolio.from_signals(prices, entries, exits, init_cash=1000.0)

    output.parent.mkdir(parents=True, exist_ok=True)
    scratch = output.parent / ".environment_roundtrip"
    frame = pd.DataFrame({"security_id": ["S1"], "value": [1.25]})
    parquet_path = scratch.with_suffix(".parquet")
    excel_path = scratch.with_suffix(".xlsx")
    try:
        frame.to_parquet(parquet_path, index=False)
        frame.to_excel(excel_path, index=False)
        parquet_ok = pd.read_parquet(parquet_path).equals(frame)
        excel_ok = pd.read_excel(excel_path).equals(frame)
    finally:
        parquet_path.unlink(missing_ok=True)
        excel_path.unlink(missing_ok=True)

    packages = {}
    for name in (
        "alphalens-reloaded", "exchange-calendars", "numpy", "openpyxl",
        "pandas", "pyarrow", "requests", "scipy", "vectorbt",
    ):
        packages[name] = importlib.metadata.version(name)
    ic_value = float(ic.iloc[0])
    ic_rows = int(ic.notna().sum())
    final_value = float(portfolio.final_value())
    observations = {
        "alphalens_spearman_ic": ic_value,
        "alphalens_rows": ic_rows,
        "vectorbt_final_value": final_value,
    }
    checks = {
        "alphalens_spearman_ic": (
            ic_rows == len(dates)
            and math.isfinite(ic_value)
            and bool(np.allclose(ic.to_numpy(), 1.0))
        ),
        "vectorbt_holding_simulation": (
            math.isfinite(final_value) and math.isclose(final_value, 1020.0)
        ),
        "parquet_roundtrip": bool(parquet_ok),
        "xlsx_roundtrip": bool(excel_ok),
        "scipy_import": bool(scipy.__version__),
    }
    payload = {
        "status": evaluate_environment_checks(checks),
        "python": platform.python_version(),
        "packages": packages,
        "checks": checks,
        "observations": observations,
        "network_market_data_requests": 0,
    }
    output.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return payload


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "preflight":
        try:
            config = json.loads(args.config.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            _emit({"status": "BLOCKED_CONFIG", "errors": ["CONFIG_READ_ERROR"], "detail": str(exc)})
            return 2
        if not isinstance(config, Mapping):
            _emit({"status": "BLOCKED_CONFIG", "errors": ["CONFIG_ROOT_NOT_OBJECT"]})
            return 2
        errors = validate_config(config, args.stage)
        payload = {"stage": args.stage, "status": errors[0] if errors else "PASS", "errors": errors}
        _emit(payload)
        return 2 if errors else 0
    if args.command == "environment-check":
        try:
            payload = _environment_check(args.output)
        except Exception as exc:
            payload = {
                "status": "FAIL",
                "errors": ["ENVIRONMENT_CHECK_ERROR"],
                "error_type": type(exc).__name__,
                "network_market_data_requests": 0,
            }
        _emit(payload)
        return 0 if payload["status"] == "PASS" else 1
    if args.command == "import-brain":
        from .registry import import_alpha_files
        _emit(import_alpha_files(args.input, args.output, args.field_catalog))
        return 0
    if args.command == "inspect-library":
        from .registry import inspect_registry
        _emit(inspect_registry(args.registry, args.output, args.field_catalog))
        return 0
    if args.command == "login-brain":
        from .brain_sync import BrainClient, login_interactive
        _emit(login_interactive(BrainClient(), args.session_file))
        return 0
    if args.command == "sync-brain":
        from .brain_sync import BrainClient
        try:
            session = json.loads(args.session_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            session = None
        cookies = session.get("cookies") if isinstance(session, Mapping) else None
        valid_cookies = isinstance(cookies, Mapping) and bool(cookies) and all(isinstance(key, str) and key and isinstance(value, str) and value for key, value in cookies.items())
        capability_verified = isinstance(session, Mapping) and session.get("metadata_capability_verified") is True
        if not valid_cookies or not capability_verified:
            _emit({"status": "AUTH_SESSION_REQUIRED", "errors": ["INVALID_SESSION"]})
            return 2
        client = BrainClient()
        client.transport.cookies.update(dict(cookies))
        client.authenticated_contract_verified = True
        _emit(client.sync_library(args.output, args.page_size, args.max_pages))
        return 0
    if args.command == "map-library":
        from .mapping import map_library
        _emit(map_library(args.registry, args.field_catalog, args.output))
        return 0
    if args.command == "convert-library":
        from .proxy_pipeline import run_proxy_pipeline
        provider_runs = {}
        for item in args.provider_run:
            if "=" not in item:
                _emit({"status": "BLOCKED_CONFIG", "errors": ["INVALID_PROVIDER_RUN"]})
                return 2
            name, path = item.split("=", 1)
            if not name or not path or name in provider_runs:
                _emit({"status": "BLOCKED_CONFIG", "errors": ["INVALID_PROVIDER_RUN"]})
                return 2
            provider_runs[name] = Path(path)
        _emit(run_proxy_pipeline(
            args.registry, args.field_mapping, provider_runs, args.output,
            args.classification_run,
        ))
        return 0
    if args.command == "probe-data":
        from .probes import probe_data
        payload = probe_data(args.provider, args.output)
        _emit(payload)
        return 0 if payload["status"] == "PASS" else 2
    if args.command == "fetch-tiingo-classifications":
        from .tiingo_classifications import (
            fetch_current_classifications,
            write_classification_bundle,
        )
        token = os.environ.get("TIINGO_API_KEY")
        if not token:
            _emit({"status": "BLOCKED_DATA_AUTH", "errors": ["TIINGO_API_KEY_REQUIRED"]})
            return 2
        try:
            frame, evidence = fetch_current_classifications(token)
            write_classification_bundle(frame, evidence, args.output)
        except (ValueError, OSError) as exc:
            _emit({"status": "BLOCKED_CLASSIFICATION_DATA", "errors": [str(exc)]})
            return 2
        _emit({
            "status": "PASS_CURRENT_ONLY",
            "row_count": len(frame),
            "historical_pit_verified": False,
            "output": str(args.output),
        })
        return 0
    _emit({"command": args.command, "status": "NOT_IMPLEMENTED"})
    return 3


if __name__ == "__main__":
    sys.exit(main())
