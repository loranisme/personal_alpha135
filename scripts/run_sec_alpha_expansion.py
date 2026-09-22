"""Standalone entry point for the detached SEC expansion workflow."""

from __future__ import annotations

import sys

from us_equity_alpha.cli import main


if __name__ == "__main__":
    raise SystemExit(main(["expand-sec-fundamentals", *sys.argv[1:]]))
