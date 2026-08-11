"""Checksum-verified Binance Vision USD-M one-minute kline ingestion."""
from __future__ import annotations

from datetime import date, timedelta
from hashlib import sha256
from pathlib import Path
import urllib.request
import pandas as pd

BASE = "https://data.binance.vision/data/futures/um/daily/klines"
COLUMNS = ["open_time", "open", "high", "low", "close", "volume", "close_time", "quote_volume", "count", "taker_buy_volume", "taker_buy_quote_volume", "ignore"]


def sha256_file(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1 << 20):
            digest.update(chunk)
    return digest.hexdigest()


def _timestamp_unit(values: pd.Series) -> str:
    """Binance archives changed from millisecond to microsecond timestamps."""
    first = int(pd.to_numeric(values, errors="raise").iloc[0])
    return "us" if abs(first) >= 10**15 else "ms"


def _download(url: str, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        urllib.request.urlretrieve(url, path)


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
    unit = _timestamp_unit(open_values)
    raw["open_time_dt"] = pd.to_datetime(open_values, unit=unit, utc=True)
    close_values = pd.to_numeric(raw["close_time"], errors="raise")
    unit = _timestamp_unit(close_values)
    raw["close_time_dt"] = pd.to_datetime(close_values, unit=unit, utc=True)
    return raw[["open_time_dt", "close_time_dt", "open", "high", "low", "close", "volume"]].sort_values("open_time_dt")


def load_range(symbol: str, start: date, end: date, cache: Path) -> pd.DataFrame:
    frames=[]; day=start
    while day<=end:
        frames.append(load_day(symbol, day, cache))
        day += timedelta(days=1)
    frame=pd.concat(frames, ignore_index=True).sort_values("open_time_dt")
    if frame["open_time_dt"].duplicated().any():
        raise RuntimeError(f"duplicate one-minute bars for {symbol}")
    expected=(end-start).days+1
    if len(frame)<expected*1430:
        raise RuntimeError(f"incomplete one-minute data for {symbol}: {len(frame)}")
    return frame


def resample(frame: pd.DataFrame, minutes: int) -> pd.DataFrame:
    indexed=frame.set_index("open_time_dt")
    out=indexed.resample(f"{minutes}min", label="left", closed="left").agg(
        open=("open","first"), high=("high","max"), low=("low","min"), close=("close","last"), volume=("volume","sum"), close_time_dt=("close_time_dt","last")
    ).dropna()
    out=out.reset_index()
    return out
