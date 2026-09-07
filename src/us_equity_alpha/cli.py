"""Command-line contracts for phase-one engineering."""

from __future__ import annotations

import argparse
import importlib.metadata
import json
import platform
import sys
from pathlib import Path
from typing import Sequence

from .contracts import STAGE_REQUIRED_FIELDS, validate_config


FUTURE_COMMANDS = {
    "sync-brain": (("--config",),),
    "import-brain": (("--input",),),
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
    return parser


def _emit(payload: dict) -> None:
    print(json.dumps(payload, indent=2, sort_keys=True))


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
    frame.to_parquet(parquet_path, index=False)
    frame.to_excel(excel_path, index=False)
    parquet_ok = pd.read_parquet(parquet_path).equals(frame)
    excel_ok = pd.read_excel(excel_path).equals(frame)
    parquet_path.unlink()
    excel_path.unlink()

    packages = {}
    for name in (
        "alphalens-reloaded", "exchange-calendars", "numpy", "openpyxl",
        "pandas", "pyarrow", "requests", "scipy", "vectorbt",
    ):
        packages[name] = importlib.metadata.version(name)
    payload = {
        "status": "PASS",
        "python": platform.python_version(),
        "packages": packages,
        "checks": {
            "alphalens_spearman_ic": float(ic.iloc[0]),
            "alphalens_rows": int(ic.notna().sum()),
            "vectorbt_final_value": float(portfolio.final_value()),
            "parquet_roundtrip": parquet_ok,
            "xlsx_roundtrip": excel_ok,
            "scipy_import": bool(scipy.__version__),
        },
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
        errors = validate_config(config, args.stage)
        payload = {"stage": args.stage, "status": errors[0] if errors else "PASS", "errors": errors}
        _emit(payload)
        return 2 if errors else 0
    if args.command == "environment-check":
        _emit(_environment_check(args.output))
        return 0
    _emit({"command": args.command, "status": "NOT_IMPLEMENTED"})
    return 3


if __name__ == "__main__":
    sys.exit(main())
