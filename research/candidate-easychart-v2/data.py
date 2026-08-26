"""Checksum-verified Binance Vision USD-M one-minute kline ingestion."""
from __future__ import annotations

from datetime import date, timedelta
from hashlib import sha256
from pathlib import Path
import random
import time
import urllib.error
import urllib.request

import pandas as pd

# NautilusTrader 1.230.0 maps ``DataFrame.values`` into a writable Cython
# memoryview. Pandas copy-on-write exposes that array as read-only, so keep
# the documented wrangler input mutable rather than rebuilding Nautilus bars.
pd.options.mode.copy_on_write = False

BASE = "https://data.binance.vision/data/futures/um/daily/klines"
COLUMNS = [
    "open_time", "open", "high", "low", "close", "volume", "close_time",
    "quote_volume", "count", "taker_buy_volume", "taker_buy_quote_volume", "ignore",
]


def sha256_file(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1 << 20):
            digest.update(chunk)
    return digest.hexdigest()


def _timestamp_unit(values: pd.Series) -> str:
    first = int(pd.to_numeric(values, errors="raise").iloc[0])
    return "us" if abs(first) >= 10**15 else "ms"


def _download(url: str, path: Path, attempts: int = 6) -> None:
    """Download once to a temporary path, retrying only transient failures."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and path.stat().st_size > 0:
        return
    temporary = path.with_suffix(path.suffix + ".part")
    temporary.unlink(missing_ok=True)
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "SMC-ICT-4-LIVE-research/1.0"},
    )
    retryable_status = {408, 425, 429, 500, 502, 503, 504}
    for attempt in range(1, attempts + 1):
        try:
            with urllib.request.urlopen(request, timeout=60) as response, temporary.open("wb") as stream:
                while chunk := response.read(1 << 20):
                    stream.write(chunk)
            if temporary.stat().st_size <= 0:
                raise RuntimeError(f"empty download from {url}")
            temporary.replace(path)
            return
        except urllib.error.HTTPError as exc:
            temporary.unlink(missing_ok=True)
            if exc.code not in retryable_status or attempt == attempts:
                raise
        except (urllib.error.URLError, TimeoutError, ConnectionError):
            temporary.unlink(missing_ok=True)
            if attempt == attempts:
                raise
        delay = min(30.0, 2.0 ** (attempt - 1)) + random.uniform(0.0, 0.5)
        time.sleep(delay)
    raise RuntimeError(f"unreachable download failure for {url}")


def load_day(symbol: str, day: date, cache: Path) -> pd.DataFrame:
    stamp = day.isoformat()
    name = f"{symbol}-1m-{stamp}.zip"
    url = f"{BASE}/{symbol}/1m/{name}"
    archive = cache / symbol / name
    checksum = archive.with_suffix(archive.suffix + ".CHECKSUM")
    _download(url, archive)
    _download(url + ".CHECKSUM", checksum)
    expected = checksum.read_text(encoding="utf-8").strip().split()[0].lower()
    actual = sha256_file(archive)
    if actual != expected:
        archive.unlink(missing_ok=True)
        raise RuntimeError(f"checksum mismatch {archive}: {actual} != {expected}")
    raw = pd.read_csv(archive, compression="zip", header=None)
    if raw.shape[1] != len(COLUMNS):
        raw = pd.read_csv(archive, compression="zip")
        if not set(COLUMNS).issubset(raw.columns):
            raise RuntimeError(f"unexpected kline schema: {list(raw.columns)}")
        raw = raw[COLUMNS]
    else:
        raw.columns = COLUMNS
        if not str(raw.iloc[0]["open_time"]).lstrip("-").isdigit():
            raw = raw.iloc[1:].copy()
    for column in ("open", "high", "low", "close", "volume"):
        raw[column] = pd.to_numeric(raw[column], errors="raise")
    open_values = pd.to_numeric(raw["open_time"], errors="raise")
    raw["open_time_dt"] = pd.to_datetime(open_values, unit=_timestamp_unit(open_values), utc=True)
    return raw[["open_time_dt", "open", "high", "low", "close", "volume"]].sort_values("open_time_dt")


def load_range(symbol: str, start: date, end: date, cache: Path) -> pd.DataFrame:
    frames: list[pd.DataFrame] = []
    day = start
    while day <= end:
        frames.append(load_day(symbol, day, cache))
        day += timedelta(days=1)
    frame = pd.concat(frames, ignore_index=True).sort_values("open_time_dt")
    if frame["open_time_dt"].duplicated().any():
        raise RuntimeError(f"duplicate one-minute bars for {symbol}")
    expected_days = (end - start).days + 1
    if len(frame) < expected_days * 1430:
        raise RuntimeError(f"incomplete one-minute data for {symbol}: {len(frame)}")
    return frame


def resample(frame: pd.DataFrame, minutes: int) -> pd.DataFrame:
    indexed = frame.set_index("open_time_dt")
    out = indexed.resample(f"{minutes}min", label="left", closed="left").agg(
        open=("open", "first"),
        high=("high", "max"),
        low=("low", "min"),
        close=("close", "last"),
        volume=("volume", "sum"),
    ).dropna()
    return out.reset_index()


def wrangler_frame(frame: pd.DataFrame, minutes: int) -> pd.DataFrame:
    data = frame[["open_time_dt", "open", "high", "low", "close", "volume"]].copy()
    data.index = pd.DatetimeIndex(data.pop("open_time_dt")) + pd.Timedelta(minutes=minutes)
    return data
