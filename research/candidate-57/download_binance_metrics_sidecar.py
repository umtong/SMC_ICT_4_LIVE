#!/usr/bin/env python3
"""Download a compact causal Binance Vision USD-M metrics sidecar."""
from __future__ import annotations

import argparse
import csv
from datetime import date, datetime, timedelta, timezone
from io import TextIOWrapper
import json
import math
from pathlib import Path
import re
import time
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
import zipfile

SYMBOLS = ("BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT")
BASE = "https://data.binance.vision/data/futures/um/daily/metrics"
NUMBER = re.compile(r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?")


def normal(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", name.strip().lower()).strip("_")


def numeric(value: Any) -> float | None:
    if value is None:
        return None
    match = NUMBER.search(str(value).replace(",", "").replace("_", ""))
    if match is None:
        return None
    result = float(match.group(0))
    return result if math.isfinite(result) else None


def timestamp_ns(value: Any) -> int | None:
    if value is None:
        return None
    text = str(value).strip()
    value_number = numeric(text)
    if value_number is not None and re.fullmatch(r"[-+]?\d+(?:\.0+)?", text):
        magnitude = abs(value_number)
        if magnitude > 1e17:
            return int(value_number)
        if magnitude > 1e14:
            return int(value_number * 1_000)
        if magnitude > 1e11:
            return int(value_number * 1_000_000)
        if magnitude > 1e9:
            return int(value_number * 1_000_000_000)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return int(parsed.timestamp() * 1_000_000_000)


def days(start: date, end: date):
    current = start
    while current <= end:
        yield current
        current += timedelta(days=1)


def fetch(url: str, cache: Path) -> bytes | None:
    if cache.is_file() and cache.stat().st_size > 0:
        return cache.read_bytes()
    request = Request(url, headers={"User-Agent": "candidate-57-research/1.0"})
    for attempt in range(4):
        try:
            with urlopen(request, timeout=45) as response:
                payload = response.read()
            if not payload:
                raise RuntimeError(f"empty Binance Vision payload: {url}")
            cache.parent.mkdir(parents=True, exist_ok=True)
            cache.write_bytes(payload)
            return payload
        except HTTPError as exc:
            if exc.code == 404:
                return None
            if attempt == 3:
                raise
        except (URLError, TimeoutError, RuntimeError):
            if attempt == 3:
                raise
        time.sleep(1.5 * (attempt + 1))
    return None


def parse_archive(payload: bytes, symbol: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    from io import BytesIO
    with zipfile.ZipFile(BytesIO(payload)) as archive:
        names = [name for name in archive.namelist() if not name.endswith("/")]
        if not names:
            return rows
        with archive.open(names[0]) as raw:
            reader = csv.DictReader(TextIOWrapper(raw, encoding="utf-8-sig", newline=""))
            for record in reader:
                normalized = {normal(key): value for key, value in record.items()}
                raw_ts = next(
                    (
                        normalized[key]
                        for key in ("create_time", "timestamp", "time", "ts")
                        if key in normalized
                    ),
                    None,
                )
                ts = timestamp_ns(raw_ts)
                taker = numeric(normalized.get("sum_taker_long_short_vol_ratio"))
                if ts is None or taker is None:
                    continue
                row: dict[str, Any] = {
                    "ts_event": ts,
                    "sum_taker_long_short_vol_ratio": taker,
                }
                for key in (
                    "sum_open_interest",
                    "sum_open_interest_value",
                    "count_long_short_ratio",
                    "count_toptrader_long_short_ratio",
                    "sum_toptrader_long_short_ratio",
                ):
                    value = numeric(normalized.get(key))
                    if value is not None:
                        row[key] = value
                rows.append(row)
    rows.sort(key=lambda row: int(row["ts_event"]))
    return rows


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", type=date.fromisoformat, required=True)
    parser.add_argument("--end", type=date.fromisoformat, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--cache", type=Path, required=True)
    args = parser.parse_args()
    if args.end < args.start:
        raise ValueError("end precedes start")

    output: dict[str, Any] = {
        "source": "Binance Vision futures/um daily metrics",
        "start": args.start.isoformat(),
        "end": args.end.isoformat(),
        "symbols": {},
        "missing": [],
    }
    for symbol in SYMBOLS:
        combined: list[dict[str, Any]] = []
        for day in days(args.start, args.end):
            filename = f"{symbol}-metrics-{day.isoformat()}.zip"
            url = f"{BASE}/{symbol}/{filename}"
            payload = fetch(url, args.cache / filename)
            if payload is None:
                output["missing"].append({"symbol": symbol, "day": day.isoformat()})
                continue
            combined.extend(parse_archive(payload, symbol))
        combined.sort(key=lambda row: int(row["ts_event"]))
        output["symbols"][symbol] = combined

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(output, separators=(",", ":"), allow_nan=False) + "\n",
        encoding="utf-8",
    )
    manifest = {
        "start": output["start"],
        "end": output["end"],
        "missing": output["missing"],
        "row_counts": {
            symbol: len(rows) for symbol, rows in output["symbols"].items()
        },
        "output_bytes": args.output.stat().st_size,
    }
    print(json.dumps(manifest, indent=2, sort_keys=True))
    return 0 if all(output["symbols"].get(symbol) for symbol in SYMBOLS) else 1


if __name__ == "__main__":
    raise SystemExit(main())
