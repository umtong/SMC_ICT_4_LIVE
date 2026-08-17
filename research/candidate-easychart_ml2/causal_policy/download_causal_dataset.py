"""Download compact, checksum-verified Binance USD-M 1m research windows.

This is a data acquisition utility, not a strategy. It preserves exact Binance
one-minute OHLC, quote volume, trade count and taker-buy fields, then writes
small period/symbol gzip CSV files for causal-policy research.
"""
from __future__ import annotations

import argparse
from datetime import date
from hashlib import sha256
import json
from pathlib import Path
import random
import time
import urllib.error
import urllib.request

import pandas as pd

BASE = "https://data.binance.vision/data/futures/um/monthly/klines"
COLUMNS = [
    "open_time", "open", "high", "low", "close", "volume", "close_time",
    "quote_volume", "count", "taker_buy_volume", "taker_buy_quote_volume", "ignore",
]
KEEP = [
    "open_time_dt", "open", "high", "low", "close", "volume", "quote_volume",
    "count", "taker_buy_volume", "taker_buy_quote_volume",
]
DEFAULT_PERIODS = {
    "may2024": ("2024-04-01", "2024-05-10"),
    "summer2024": ("2024-07-01", "2024-08-10"),
    "feb2025": ("2025-01-01", "2025-02-10"),
    "aug2025": ("2025-07-10", "2025-08-20"),
    "nov2025": ("2025-10-01", "2025-11-10"),
    "feb2026": ("2026-01-01", "2026-02-10"),
}


def _sha256(path: Path) -> str:
    h = sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _download(url: str, path: Path, attempts: int = 6) -> None:
    if path.exists() and path.stat().st_size > 0:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".part")
    tmp.unlink(missing_ok=True)
    req = urllib.request.Request(url, headers={"User-Agent": "SMC-ICT-4-LIVE-causal-research/1.0"})
    retryable = {408, 425, 429, 500, 502, 503, 504}
    for attempt in range(attempts):
        try:
            with urllib.request.urlopen(req, timeout=120) as response, tmp.open("wb") as out:
                while chunk := response.read(1 << 20):
                    out.write(chunk)
            if tmp.stat().st_size <= 0:
                raise RuntimeError(f"empty download: {url}")
            tmp.replace(path)
            return
        except urllib.error.HTTPError as exc:
            tmp.unlink(missing_ok=True)
            if exc.code not in retryable or attempt + 1 == attempts:
                raise
        except (urllib.error.URLError, TimeoutError, ConnectionError):
            tmp.unlink(missing_ok=True)
            if attempt + 1 == attempts:
                raise
        time.sleep(min(30.0, 2.0 ** attempt) + random.uniform(0.0, 0.5))
    raise RuntimeError(f"download failed: {url}")


def _months(start: pd.Timestamp, end: pd.Timestamp):
    cursor = pd.Timestamp(start.year, start.month, 1, tz="UTC")
    last = pd.Timestamp(end.year, end.month, 1, tz="UTC")
    while cursor <= last:
        yield cursor.strftime("%Y-%m")
        cursor += pd.offsets.MonthBegin(1)


def load_month(symbol: str, month: str, cache: Path) -> pd.DataFrame:
    name = f"{symbol}-1m-{month}.zip"
    url = f"{BASE}/{symbol}/1m/{name}"
    archive = cache / symbol / name
    checksum = archive.with_suffix(archive.suffix + ".CHECKSUM")
    _download(url, archive)
    _download(url + ".CHECKSUM", checksum)
    expected = checksum.read_text(encoding="utf-8").strip().split()[0].lower()
    actual = _sha256(archive)
    if actual != expected:
        archive.unlink(missing_ok=True)
        raise RuntimeError(f"checksum mismatch for {archive}: {actual} != {expected}")
    raw = pd.read_csv(archive, compression="zip", header=None)
    if raw.shape[1] != len(COLUMNS):
        raw = pd.read_csv(archive, compression="zip")
        if not set(COLUMNS).issubset(raw.columns):
            raise RuntimeError(f"unexpected schema for {archive}: {list(raw.columns)}")
        raw = raw[COLUMNS]
    else:
        raw.columns = COLUMNS
        if not str(raw.iloc[0]["open_time"]).lstrip("-").isdigit():
            raw = raw.iloc[1:].copy()
    for col in COLUMNS[:-1]:
        raw[col] = pd.to_numeric(raw[col], errors="raise")
    first = int(raw["open_time"].iloc[0])
    unit = "us" if abs(first) >= 10**15 else "ms"
    raw["open_time_dt"] = pd.to_datetime(raw["open_time"], unit=unit, utc=True)
    raw["count"] = raw["count"].astype("int64")
    return raw[KEEP].sort_values("open_time_dt")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cache", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--symbols", nargs="+", default=["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT"])
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    manifest = {"source": "Binance Vision USD-M monthly 1m klines", "periods": {}, "files": {}}
    for period, (start_text, end_text) in DEFAULT_PERIODS.items():
        start = pd.Timestamp(start_text, tz="UTC")
        end_exclusive = pd.Timestamp(end_text, tz="UTC") + pd.Timedelta(days=1)
        manifest["periods"][period] = {"start": start_text, "end": end_text}
        for symbol in args.symbols:
            frames = [load_month(symbol, month, args.cache) for month in _months(start, end_exclusive - pd.Timedelta(minutes=1))]
            frame = pd.concat(frames, ignore_index=True)
            frame = frame[(frame["open_time_dt"] >= start) & (frame["open_time_dt"] < end_exclusive)].copy()
            frame = frame.sort_values("open_time_dt").drop_duplicates("open_time_dt")
            expected = int((end_exclusive - start).total_seconds() // 60)
            if len(frame) < expected - 10:
                raise RuntimeError(f"incomplete {period}/{symbol}: {len(frame)} < {expected - 10}")
            out = args.output / period / f"{symbol}.csv.gz"
            out.parent.mkdir(parents=True, exist_ok=True)
            frame.to_csv(out, index=False, compression="gzip", float_format="%.10g")
            key = f"{period}/{symbol}"
            manifest["files"][key] = {
                "path": str(out.relative_to(args.output)),
                "rows": int(len(frame)),
                "first": str(frame["open_time_dt"].iloc[0]),
                "last": str(frame["open_time_dt"].iloc[-1]),
                "sha256": _sha256(out),
            }
            print(key, len(frame), out, flush=True)
    (args.output / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")


if __name__ == "__main__":
    main()
