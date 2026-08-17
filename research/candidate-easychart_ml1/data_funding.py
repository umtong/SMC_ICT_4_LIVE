"""Checksum-verified Binance USD-M monthly funding settlements."""
from __future__ import annotations

from datetime import date
from pathlib import Path

import pandas as pd

from data import _download, sha256_file

BASE = "https://data.binance.vision/data/futures/um/monthly/fundingRate"
COLUMNS = ["calc_time", "funding_interval_hours", "last_funding_rate"]


def _months(start: date, end: date) -> list[str]:
    current = pd.Timestamp(start).to_period("M")
    final = pd.Timestamp(end).to_period("M")
    output: list[str] = []
    while current <= final:
        output.append(str(current))
        current += 1
    return output


def load_funding_month(symbol: str, month: str, cache: Path) -> pd.DataFrame:
    name = f"{symbol}-fundingRate-{month}.zip"
    url = f"{BASE}/{symbol}/{name}"
    archive = cache / "funding" / symbol / name
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
        lowered = {str(column).strip().lower(): column for column in raw.columns}
        if not set(COLUMNS).issubset(lowered):
            raise RuntimeError(f"unexpected funding schema: {list(raw.columns)}")
        raw = raw[[lowered[name] for name in COLUMNS]]
        raw.columns = COLUMNS
    else:
        raw.columns = COLUMNS
        if str(raw.iloc[0]["calc_time"]).strip().lower() == "calc_time":
            raw = raw.iloc[1:].copy()
    values = pd.to_numeric(raw["calc_time"], errors="coerce")
    raw["funding_ts"] = pd.to_datetime(values, unit="ms", utc=True, errors="coerce")
    raw["funding_interval_hours"] = pd.to_numeric(raw["funding_interval_hours"], errors="coerce")
    raw["funding_rate"] = pd.to_numeric(raw["last_funding_rate"], errors="coerce")
    return (
        raw[["funding_ts", "funding_interval_hours", "funding_rate"]]
        .dropna(subset=["funding_ts", "funding_rate"])
        .sort_values("funding_ts")
        .drop_duplicates("funding_ts", keep="last")
    )


def load_funding_range(symbol: str, start: date, end: date, cache: Path) -> pd.DataFrame:
    frames = [load_funding_month(symbol, month, cache) for month in _months(start, end)]
    frame = pd.concat(frames, ignore_index=True).sort_values("funding_ts")
    start_ts = pd.Timestamp(start, tz="UTC")
    end_ts = pd.Timestamp(end, tz="UTC") + pd.Timedelta(days=1)
    return frame.loc[(frame.funding_ts >= start_ts) & (frame.funding_ts < end_ts)].reset_index(drop=True)


def funding_return(
    funding: pd.DataFrame,
    side: int,
    entry_ts: pd.Timestamp,
    exit_ts: pd.Timestamp,
) -> float:
    """Return account-price return from settlements while the position is open.

    Positive rates transfer value from longs to shorts. A long therefore earns
    ``-rate`` and a short earns ``+rate`` at each settlement.
    """
    if funding.empty or exit_ts <= entry_ts:
        return 0.0
    paid = funding.loc[(funding.funding_ts > entry_ts) & (funding.funding_ts <= exit_ts)]
    return float((-side * paid.funding_rate.astype(float)).sum())
