"""Download and aggregate a deterministic BTCUSDT aggTrades week to one-second observations."""

from __future__ import annotations

import argparse
import csv
from dataclasses import asdict
from datetime import date, timedelta, datetime, timezone
import gzip
import json
from pathlib import Path
from typing import Any

from aggtrade_probe import (
    BASE,
    aggregate_minutes,
    aggregate_seconds,
    csv_text,
    download,
    parse_klines,
    reconcile,
    verify_checksum,
)


def process_day(symbol: str, day: str) -> tuple[list[Any], dict[str, Any]]:
    agg_name = f"{symbol}-aggTrades-{day}.zip"
    agg_url = f"{BASE}/aggTrades/{symbol}/{agg_name}"
    agg_payload = download(agg_url)
    agg_sha = verify_checksum(agg_payload, download(f"{agg_url}.CHECKSUM"), agg_name)
    seconds, diagnostics = aggregate_seconds(csv_text(agg_payload, agg_name))

    kline_name = f"{symbol}-1m-{day}.zip"
    kline_url = f"{BASE}/klines/{symbol}/1m/{kline_name}"
    kline_payload = download(kline_url)
    kline_sha = verify_checksum(kline_payload, download(f"{kline_url}.CHECKSUM"), kline_name)
    klines = parse_klines(csv_text(kline_payload, kline_name))
    recon = reconcile(aggregate_minutes(seconds), klines)
    return seconds, {
        "date": day,
        "aggtrade_zip_sha256": agg_sha,
        "aggtrade_zip_bytes": len(agg_payload),
        "kline_zip_sha256": kline_sha,
        "kline_zip_bytes": len(kline_payload),
        "event_stream": diagnostics,
        "one_second_rows": len(seconds),
        "reconciliation": recon,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbol", default="BTCUSDT")
    parser.add_argument("--start-date", default="2024-10-13")
    parser.add_argument("--days", type=int, default=8)
    parser.add_argument("--output", default="artifacts/candidate-09-aggweek")
    args = parser.parse_args()

    symbol = args.symbol.upper()
    start = date.fromisoformat(args.start_date)
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    csv_path = output / f"{symbol}-1s-{start.isoformat()}_{(start + timedelta(days=args.days - 1)).isoformat()}.csv.gz"

    daily: list[dict[str, Any]] = []
    total_rows = 0
    with gzip.open(csv_path, "wt", encoding="utf-8", newline="", compresslevel=6) as stream:
        writer: csv.DictWriter | None = None
        for offset in range(args.days):
            day = (start + timedelta(days=offset)).isoformat()
            seconds, summary = process_day(symbol, day)
            if not seconds:
                raise ValueError(f"{day}: no seconds")
            if writer is None:
                writer = csv.DictWriter(stream, fieldnames=list(asdict(seconds[0]).keys()))
                writer.writeheader()
            for bar in seconds:
                writer.writerow(asdict(bar))
            total_rows += len(seconds)
            daily.append(summary)
            print(json.dumps({"completed_day": day, "rows": len(seconds), "zip_bytes": summary["aggtrade_zip_bytes"]}, sort_keys=True), flush=True)

    manifest = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "symbol": symbol,
        "start_date": start.isoformat(),
        "days": args.days,
        "end_date": (start + timedelta(days=args.days - 1)).isoformat(),
        "total_nonempty_seconds": total_rows,
        "output_file": csv_path.name,
        "daily": daily,
        "timestamp_contract": "second_ns is the completed one-second event bucket; decisions may use it only after that second ends",
    }
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(manifest, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
