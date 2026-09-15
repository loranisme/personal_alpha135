"""Download a single hashed Tiingo SPY history using a hidden token prompt."""

import argparse
import getpass
import json
from pathlib import Path

from us_equity_alpha.history_verification import download_tiingo_benchmark


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--start-date", required=True)
    parser.add_argument("--end-date", required=True)
    args = parser.parse_args()
    token = getpass.getpass("Tiingo API token (hidden, memory only): ")
    evidence = download_tiingo_benchmark(
        args.output,
        token=token,
        start_date=args.start_date,
        end_date=args.end_date,
    )
    evidence_path = args.output.with_suffix(".evidence.json")
    evidence_path.write_text(
        json.dumps(evidence, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    )
    print(json.dumps({"status": "PASS", **evidence}, indent=2))


if __name__ == "__main__":
    main()
