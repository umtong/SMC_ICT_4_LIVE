#!/usr/bin/env python3
"""Harvest Binance aggregate book-depth features without future information.

The public bookDepth archive is a roughly 30-second aggregate depth profile at
percentage bands, not a replayable top-of-book.  This script therefore uses it
only for the information it actually contains: side asymmetry, depth shape,
and replenishment/depletion between consecutive snapshots.  Rows whose implied
price is inconsistent with the synchronized one-minute futures candle are
excluded rather than silently repaired.
"""
from __future__ import annotations

import argparse
from datetime import date, datetime, timedelta, timezone
from io import BytesIO
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import urlopen
import gzip
import json
import math
import zipfile

import numpy as np
import pandas as pd

BASE = "https://data.binance.vision/data/futures/um/daily"
PERCENTAGES = [-5, -4, -3, -2, -1, 1, 2, 3, 4, 5]


def _days(start: str, end: str):
    current = date.fromisoformat(start)
    terminal = date.fromisoformat(end)
    while current < terminal:
        yield current
        current += timedelta(days=1)


def _download(url: str, cache: Path) -> bytes:
    cache.parent.mkdir(parents=True, exist_ok=True)
    if cache.exists():
        return cache.read_bytes()
    try:
        with urlopen(url, timeout=180) as response:
            data = response.read()
    except HTTPError as error:
        raise RuntimeError(f"download failed {error.code}: {url}") from error
    cache.write_bytes(data)
    return data


def _read_zip_csv(blob: bytes) -> pd.DataFrame:
    with zipfile.ZipFile(BytesIO(blob)) as archive:
        names = [name for name in archive.namelist() if name.lower().endswith(".csv")]
        if len(names) != 1:
            raise RuntimeError(f"expected one CSV, found {names}")
        with archive.open(names[0]) as file:
            return pd.read_csv(file)


def _read_klines(symbol: str, day: date, cache: Path) -> pd.DataFrame:
    stamp = day.isoformat()
    name = f"{symbol}-1m-{stamp}.zip"
    url = f"{BASE}/klines/{symbol}/1m/{name}"
    raw = _read_zip_csv(_download(url, cache / "klines" / symbol / name))
    expected = [
        "open_time", "open", "high", "low", "close", "volume", "close_time",
        "quote_volume", "count", "taker_buy_volume", "taker_buy_quote_volume", "ignore",
    ]
    if list(raw.columns) != expected:
        if len(raw.columns) == 12 and str(raw.columns[0]).isdigit():
            # Some archive generations have no header; reread from the raw bytes.
            blob = _download(url, cache / "klines" / symbol / name)
            with zipfile.ZipFile(BytesIO(blob)) as archive:
                csv_name = next(n for n in archive.namelist() if n.lower().endswith(".csv"))
                with archive.open(csv_name) as file:
                    raw = pd.read_csv(file, header=None, names=expected)
        elif len(raw.columns) == 12:
            raw.columns = expected
        else:
            raise RuntimeError(f"unexpected kline schema: {list(raw.columns)}")
    raw["timestamp"] = pd.to_datetime(pd.to_numeric(raw.open_time), unit="ms", utc=True)
    numeric = ["open", "high", "low", "close", "quote_volume", "taker_buy_quote_volume"]
    for column in numeric:
        raw[column] = pd.to_numeric(raw[column], errors="coerce")
    raw["signed_quote_flow"] = 2.0 * raw.taker_buy_quote_volume - raw.quote_volume
    raw["flow_share"] = raw.signed_quote_flow / raw.quote_volume.clip(lower=1e-12)
    return raw.set_index("timestamp")[["open", "high", "low", "close", "quote_volume", "flow_share"]]


def _read_bookdepth(symbol: str, day: date, cache: Path) -> pd.DataFrame:
    stamp = day.isoformat()
    name = f"{symbol}-bookDepth-{stamp}.zip"
    url = f"{BASE}/bookDepth/{symbol}/{name}"
    raw = _read_zip_csv(_download(url, cache / "bookDepth" / symbol / name))
    raw.columns = [str(column).strip().lower() for column in raw.columns]
    required = {"timestamp", "percentage", "depth", "notional"}
    if not required.issubset(raw.columns):
        raise RuntimeError(f"unexpected bookDepth schema: {list(raw.columns)}")
    raw["timestamp"] = pd.to_datetime(raw.timestamp, utc=True, errors="coerce")
    for column in ("percentage", "depth", "notional"):
        raw[column] = pd.to_numeric(raw[column], errors="coerce")
    raw = raw.dropna(subset=["timestamp", "percentage", "depth", "notional"])
    raw = raw[raw.percentage.isin(PERCENTAGES)]
    return raw


def _minute_features(book: pd.DataFrame, klines: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    notional = book.pivot_table(index="timestamp", columns="percentage", values="notional", aggfunc="last")
    depth = book.pivot_table(index="timestamp", columns="percentage", values="depth", aggfunc="last")
    notional = notional.reindex(columns=PERCENTAGES).sort_index()
    depth = depth.reindex(columns=PERCENTAGES).sort_index()

    feature = pd.DataFrame(index=notional.index)
    for level in (1, 2, 3, 4, 5):
        bid = notional[-level].clip(lower=0.0)
        ask = notional[level].clip(lower=0.0)
        feature[f"bid_notional_{level}"] = bid
        feature[f"ask_notional_{level}"] = ask
        feature[f"depth_imbalance_{level}"] = (bid - ask) / (bid + ask).clip(lower=1e-12)

    bid_price = notional[-1] / depth[-1].clip(lower=1e-12)
    ask_price = notional[1] / depth[1].clip(lower=1e-12)
    feature["depth_price_proxy"] = (bid_price + ask_price) / 2.0
    feature["bid_depth_slope"] = (notional[-5] - notional[-1]) / notional[-1].clip(lower=1e-12)
    feature["ask_depth_slope"] = (notional[5] - notional[1]) / notional[1].clip(lower=1e-12)
    feature["slope_asymmetry"] = feature.bid_depth_slope - feature.ask_depth_slope

    minute = feature.resample("1min").agg(["first", "last", "min", "max"])
    minute.columns = [f"{name}_{aggregation}" for name, aggregation in minute.columns]
    minute = minute.join(klines, how="inner")

    proxy_error = np.abs(np.log(minute.depth_price_proxy_last / minute.close.clip(lower=1e-12)))
    minute["depth_price_log_error"] = proxy_error
    valid = np.isfinite(proxy_error) & (proxy_error <= math.log(1.10))
    raw_rows = len(minute)
    minute = minute.loc[valid].copy()

    for level in (1, 2, 3, 4, 5):
        bid = minute[f"bid_notional_{level}_last"].clip(lower=1e-12)
        ask = minute[f"ask_notional_{level}_last"].clip(lower=1e-12)
        minute[f"bid_log_change_{level}"] = np.log(bid).diff()
        minute[f"ask_log_change_{level}"] = np.log(ask).diff()
        minute[f"net_replenishment_{level}"] = minute[f"bid_log_change_{level}"] - minute[f"ask_log_change_{level}"]
        minute[f"bid_intraminute_replenishment_{level}"] = (
            minute[f"bid_notional_{level}_last"] - minute[f"bid_notional_{level}_min"]
        ) / minute[f"bid_notional_{level}_first"].clip(lower=1e-12)
        minute[f"ask_intraminute_replenishment_{level}"] = (
            minute[f"ask_notional_{level}_last"] - minute[f"ask_notional_{level}_min"]
        ) / minute[f"ask_notional_{level}_first"].clip(lower=1e-12)

    minute["log_return_1m"] = np.log(minute.close / minute.close.shift(1))
    minute["price_range_bps"] = (minute.high - minute.low) / minute.close.clip(lower=1e-12) * 10_000.0
    minute["close_location"] = (minute.close - minute.low) / (minute.high - minute.low).clip(lower=1e-12)
    minute = minute.replace([np.inf, -np.inf], np.nan)

    metadata = {
        "joined_minutes_before_validity_filter": raw_rows,
        "valid_minutes": len(minute),
        "valid_fraction": len(minute) / max(raw_rows, 1),
        "first_timestamp": str(minute.index.min()) if len(minute) else None,
        "last_timestamp": str(minute.index.max()) if len(minute) else None,
    }
    return minute, metadata


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--start", required=True, help="inclusive YYYY-MM-DD")
    parser.add_argument("--end", required=True, help="exclusive YYYY-MM-DD")
    parser.add_argument("--cache", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    frames = []
    days = []
    for day in _days(args.start, args.end):
        klines = _read_klines(args.symbol, day, args.cache)
        book = _read_bookdepth(args.symbol, day, args.cache)
        features, metadata = _minute_features(book, klines)
        features["symbol"] = args.symbol
        frames.append(features.reset_index())
        days.append({"date": day.isoformat(), **metadata})

    output = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    args.output.mkdir(parents=True, exist_ok=True)
    output.to_csv(args.output / "bookdepth_features.csv.gz", index=False, compression="gzip")
    with (args.output / "metadata.json").open("w") as file:
        json.dump(
            {
                "symbol": args.symbol,
                "start": args.start,
                "end": args.end,
                "rows": len(output),
                "days": days,
                "generated_at": datetime.now(timezone.utc).isoformat(),
            },
            file,
            indent=2,
        )
        file.write("\n")


if __name__ == "__main__":
    main()
