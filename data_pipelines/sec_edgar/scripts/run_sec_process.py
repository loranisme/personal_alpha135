#!/usr/bin/env python3
"""Process an immutable SEC raw snapshot into deterministic PIT artifacts."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from sec_edgar_pipeline.processing import process_sec_raw


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--raw-root", type=Path, required=True)
    parser.add_argument("--mapping", type=Path, required=True)
    parser.add_argument("--derived-definitions", type=Path, required=True)
    parser.add_argument("--as-of", required=True)
    parser.add_argument("--start-date", default="2019-01-01")
    parser.add_argument("--end-date")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--succession-predecessor-root", type=Path)
    parser.add_argument("--succession-filing-root", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    summary = process_sec_raw(
        raw_root=args.raw_root,
        mapping_path=args.mapping,
        derived_definitions_path=args.derived_definitions,
        output_dir=args.output or args.raw_root / "processed",
        as_of=args.as_of,
        start_date=args.start_date,
        end_date=args.end_date,
        succession_predecessor_root=args.succession_predecessor_root,
        succession_filing_root=args.succession_filing_root,
    )
    print(json.dumps(summary, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
