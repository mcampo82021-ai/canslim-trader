"""
audit_watchlist.py — Batch technical audit for CAN SLIM candidates.

Default watchlist comes from the current tracker:
PAYS, ABX, DDI, OPRA, VOXR.
"""

from __future__ import annotations

import argparse
import csv
import json
from datetime import datetime
from pathlib import Path

from technical_audit import audit_bars, fetch_yfinance_bars


DEFAULT_WATCHLIST = ["PAYS", "ABX", "DDI", "OPRA", "VOXR"]
OUTPUT_DIR = Path("reports") / datetime.now().strftime("%Y-%m-%d")


def audit_ticker(ticker: str, include_benchmark: bool) -> dict:
    bars = fetch_yfinance_bars(ticker)
    benchmark_bars = fetch_yfinance_bars("SPY") if include_benchmark else None
    audit = audit_bars(ticker, bars, benchmark_bars)
    return audit.as_dict()


def save_outputs(rows: list[dict], output_dir: Path) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / "technical_watchlist_audit.json"
    csv_path = output_dir / "technical_watchlist_audit.csv"

    with json_path.open("w", encoding="utf-8") as file:
        json.dump(rows, file, ensure_ascii=False, indent=2)

    fieldnames = [
        "ticker",
        "verdict",
        "close",
        "sma50",
        "sma200",
        "trend_ok",
        "pivot",
        "pct_from_pivot",
        "max_52w",
        "pct_from_52w_high",
        "avg_vol_50d",
        "latest_volume",
        "latest_volume_vs_50d_pct",
        "pivot_volume_vs_50d_pct",
        "pivot_volume_valid",
        "rs_line_new_high",
        "stage",
        "pattern",
    ]
    with csv_path.open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: row.get(key) for key in fieldnames})

    return json_path, csv_path


def print_table(rows: list[dict]) -> None:
    headers = [
        "Ticker",
        "Verdict",
        "Close",
        "Pivot",
        "%Pivot",
        "Trend",
        "RS High",
        "Stage",
        "Pattern",
    ]
    print("\n" + " | ".join(headers))
    print("-" * 118)
    for row in rows:
        values = [
            row.get("ticker"),
            row.get("verdict"),
            row.get("close"),
            row.get("pivot"),
            row.get("pct_from_pivot"),
            row.get("trend_ok"),
            row.get("rs_line_new_high"),
            row.get("stage"),
            row.get("pattern"),
        ]
        print(" | ".join(str(value) for value in values))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Audit CAN SLIM watchlist technicals")
    parser.add_argument("tickers", nargs="*", help="Tickers to audit")
    parser.add_argument(
        "--no-benchmark",
        action="store_true",
        help="Skip SPY benchmark and RS Line new-high check",
    )
    parser.add_argument(
        "--output-dir",
        default=str(OUTPUT_DIR),
        help="Directory for JSON/CSV outputs",
    )
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    tickers = [ticker.upper() for ticker in (args.tickers or DEFAULT_WATCHLIST)]

    rows: list[dict] = []
    for ticker in tickers:
        print(f"Auditando {ticker}...")
        try:
            rows.append(audit_ticker(ticker, include_benchmark=not args.no_benchmark))
        except Exception as exc:
            rows.append({"ticker": ticker, "verdict": "ERROR", "error": str(exc)})

    print_table(rows)
    json_path, csv_path = save_outputs(rows, Path(args.output_dir))
    print(f"\nJSON: {json_path}")
    print(f"CSV:  {csv_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
