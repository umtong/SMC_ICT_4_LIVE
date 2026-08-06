"""Build checksum-verified bookDepth and derivatives metrics context for a fixed week.

The output is raw-but-normalized context. No strategy thresholds or outcome labels
are calculated here. Every timestamp is preserved, so later joins must use the
latest observation whose timestamp is not after the decision time.
"""

from __future__ import annotations

import argparse
import csv
from datetime import date, datetime, timedelta, timezone
import gzip
import io
import json
from pathlib import Path
from typing import Any

from aggtrade_probe import BASE, csv_text, download, verify_checksum

DEPTH_BANDS = (-5, -4, -3, -2, -1, 1, 2, 3, 4, 5)
METRIC_COLUMNS = (
    "create_time",
    "symbol",
    "sum_open_interest",
    "sum_open_interest_value",
    "count_toptrader_long_short_ratio",
    "sum_toptrader_long_short_ratio",
    "count_long_short_ratio",
    "sum_taker_long_short_vol_ratio",
)


def parse_utc_ns(raw: str) -> int:
    value = raw.strip()
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    else:
        parsed = parsed.astimezone(timezone.utc)
    return int(parsed.timestamp() * 1_000_000_000)


def fetch_text(data_type: str, symbol: str, day: str) -> tuple[str, dict[str, Any]]:
    filename = f"{symbol}-{data_type}-{day}.zip"
    url = f"{BASE}/{data_type}/{symbol}/{filename}"
    payload = download(url)
    checksum = verify_checksum(payload, download(f"{url}.CHECKSUM"), filename)
    return csv_text(payload, filename), {
        "data_type": data_type,
        "date": day,
        "url": url,
        "sha256": checksum,
        "zip_bytes": len(payload),
    }


def parse_depth(text: str, source: str) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    reader = csv.DictReader(io.StringIO(text))
    expected = ["timestamp", "percentage", "depth", "notional"]
    if reader.fieldnames != expected:
        raise ValueError(f"{source}: columns={reader.fieldnames}, expected={expected}")

    grouped: dict[int, dict[int, tuple[float, float]]] = {}
    row_count = 0
    duplicate_bands = 0
    for row in reader:
        row_count += 1
        timestamp_ns = parse_utc_ns(row["timestamp"])
        percentage = int(row["percentage"])
        if percentage not in DEPTH_BANDS:
            raise ValueError(f"{source}: unexpected depth band {percentage}")
        depth = float(row["depth"])
        notional = float(row["notional"])
        if depth < 0.0 or notional < 0.0:
            raise ValueError(f"{source}: negative depth/notional")
        bucket = grouped.setdefault(timestamp_ns, {})
        if percentage in bucket:
            duplicate_bands += 1
        bucket[percentage] = (depth, notional)

    records: list[dict[str, Any]] = []
    missing_snapshot_count = 0
    for timestamp_ns in sorted(grouped):
        bucket = grouped[timestamp_ns]
        if set(bucket) != set(DEPTH_BANDS):
            missing_snapshot_count += 1
            continue
        record: dict[str, Any] = {"timestamp_ns": timestamp_ns}
        for percentage in DEPTH_BANDS:
            side = "bid" if percentage < 0 else "ask"
            band = abs(percentage)
            depth, notional = bucket[percentage]
            record[f"depth_{side}_{band}"] = depth
            record[f"notional_{side}_{band}"] = notional
        records.append(record)

    if not records:
        raise ValueError(f"{source}: no complete depth snapshots")
    gaps = [
        (records[index]["timestamp_ns"] - records[index - 1]["timestamp_ns"]) / 1_000_000_000
        for index in range(1, len(records))
    ]
    diagnostics = {
        "raw_rows": row_count,
        "complete_snapshots": len(records),
        "missing_or_incomplete_snapshots": missing_snapshot_count,
        "duplicate_band_rows": duplicate_bands,
        "first_timestamp_ns": records[0]["timestamp_ns"],
        "last_timestamp_ns": records[-1]["timestamp_ns"],
        "minimum_gap_seconds": min(gaps) if gaps else None,
        "maximum_gap_seconds": max(gaps) if gaps else None,
        "gaps_over_60_seconds": sum(1 for gap in gaps if gap > 60.0),
    }
    return records, diagnostics


def parse_metrics(text: str, source: str, symbol: str) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    reader = csv.DictReader(io.StringIO(text))
    if tuple(reader.fieldnames or ()) != METRIC_COLUMNS:
        raise ValueError(f"{source}: columns={reader.fieldnames}, expected={METRIC_COLUMNS}")

    records: list[dict[str, Any]] = []
    last_timestamp_ns = -1
    duplicate_timestamps = 0
    for row in reader:
        if row["symbol"] != symbol:
            raise ValueError(f"{source}: unexpected symbol {row['symbol']}")
        timestamp_ns = parse_utc_ns(row["create_time"])
        if timestamp_ns < last_timestamp_ns:
            raise ValueError(f"{source}: timestamp regression")
        if timestamp_ns == last_timestamp_ns:
            duplicate_timestamps += 1
        last_timestamp_ns = timestamp_ns
        record: dict[str, Any] = {"timestamp_ns": timestamp_ns, "symbol": symbol}
        for field in METRIC_COLUMNS[2:]:
            value = float(row[field])
            if value < 0.0:
                raise ValueError(f"{source}: negative metric {field}")
            record[field] = value
        records.append(record)

    if not records:
        raise ValueError(f"{source}: no metric rows")
    gaps = [
        (records[index]["timestamp_ns"] - records[index - 1]["timestamp_ns"]) / 1_000_000_000
        for index in range(1, len(records))
    ]
    diagnostics = {
        "rows": len(records),
        "duplicate_timestamps": duplicate_timestamps,
        "first_timestamp_ns": records[0]["timestamp_ns"],
        "last_timestamp_ns": records[-1]["timestamp_ns"],
        "minimum_gap_seconds": min(gaps) if gaps else None,
        "maximum_gap_seconds": max(gaps) if gaps else None,
        "gaps_over_600_seconds": sum(1 for gap in gaps if gap > 600.0),
    }
    return records, diagnostics


def write_csv_gz(path: Path, records: list[dict[str, Any]]) -> None:
    if not records:
        raise ValueError(f"cannot write empty dataset: {path}")
    with gzip.open(path, "wt", encoding="utf-8", newline="", compresslevel=6) as stream:
        writer = csv.DictWriter(stream, fieldnames=list(records[0]))
        writer.writeheader()
        writer.writerows(records)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbol", default="BTCUSDT")
    parser.add_argument("--start-date", default="2024-10-13")
    parser.add_argument("--days", type=int, default=8)
    parser.add_argument("--output", default="artifacts/candidate-09-context-week")
    args = parser.parse_args()

    symbol = args.symbol.upper()
    start = date.fromisoformat(args.start_date)
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)

    all_depth: list[dict[str, Any]] = []
    all_metrics: list[dict[str, Any]] = []
    daily: list[dict[str, Any]] = []

    for offset in range(args.days):
        day = (start + timedelta(days=offset)).isoformat()
        depth_text, depth_source = fetch_text("bookDepth", symbol, day)
        depth_rows, depth_diagnostics = parse_depth(depth_text, f"{symbol}-bookDepth-{day}")
        metrics_text, metrics_source = fetch_text("metrics", symbol, day)
        metrics_rows, metrics_diagnostics = parse_metrics(
            metrics_text,
            f"{symbol}-metrics-{day}",
            symbol,
        )
        all_depth.extend(depth_rows)
        all_metrics.extend(metrics_rows)
        daily.append(
            {
                "date": day,
                "book_depth": {**depth_source, **depth_diagnostics},
                "metrics": {**metrics_source, **metrics_diagnostics},
            }
        )
        print(
            json.dumps(
                {
                    "completed_day": day,
                    "depth_snapshots": len(depth_rows),
                    "metric_rows": len(metrics_rows),
                },
                sort_keys=True,
            ),
            flush=True,
        )

    if any(all_depth[index]["timestamp_ns"] <= all_depth[index - 1]["timestamp_ns"] for index in range(1, len(all_depth))):
        raise ValueError("combined bookDepth timestamps are not strictly increasing")
    if any(all_metrics[index]["timestamp_ns"] <= all_metrics[index - 1]["timestamp_ns"] for index in range(1, len(all_metrics))):
        raise ValueError("combined metrics timestamps are not strictly increasing")

    end = start + timedelta(days=args.days - 1)
    depth_name = f"{symbol}-bookDepth-{start.isoformat()}_{end.isoformat()}.csv.gz"
    metrics_name = f"{symbol}-metrics-{start.isoformat()}_{end.isoformat()}.csv.gz"
    write_csv_gz(output / depth_name, all_depth)
    write_csv_gz(output / metrics_name, all_metrics)

    manifest = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "symbol": symbol,
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "days": args.days,
        "depth_snapshots": len(all_depth),
        "metric_rows": len(all_metrics),
        "depth_file": depth_name,
        "metrics_file": metrics_name,
        "depth_semantics": "cumulative depth and notional within each signed percentage band; negative bands are bids below reference price and positive bands are asks above it",
        "causal_join_contract": "for a decision timestamp t, use only the latest context row with timestamp_ns <= t; never backfill from a later row",
        "daily": daily,
    }
    (output / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(manifest, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
