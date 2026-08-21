"""Checksum-verified Binance USD-M five-minute positioning metrics."""
from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd


BASE = "https://data.binance.vision/data/futures/um/daily/metrics"
COLUMNS = [
    "create_time",
    "symbol",
    "sum_open_interest",
    "sum_open_interest_value",
    "count_toptrader_long_short_ratio",
    "sum_toptrader_long_short_ratio",
    "count_long_short_ratio",
    "sum_taker_long_short_vol_ratio",
]


def _parse_time(values: pd.Series) -> pd.DatetimeIndex:
    numeric = pd.to_numeric(values, errors="coerce")
    if numeric.notna().mean() > 0.95:
        first = int(numeric.dropna().iloc[0])
        if abs(first) >= 10**15:
            unit = "us"
        elif abs(first) >= 10**12:
            unit = "ms"
        else:
            unit = "s"
        return pd.DatetimeIndex(pd.to_datetime(numeric, unit=unit, utc=True))
    return pd.DatetimeIndex(pd.to_datetime(values, utc=True, errors="raise"))


def load_day_metrics(symbol: str, day: date, cache: Path) -> pd.DataFrame:
    from data import _download, sha256_file
    stamp = day.isoformat()
    name = f"{symbol}-metrics-{stamp}.zip"
    url = f"{BASE}/{symbol}/{name}"
    archive = cache / "metrics" / symbol / name
    checksum = archive.with_suffix(archive.suffix + ".CHECKSUM")
    _download(url, archive)
    _download(url + ".CHECKSUM", checksum)
    expected = checksum.read_text(encoding="utf-8").strip().split()[0].lower()
    actual = sha256_file(archive)
    if actual != expected:
        archive.unlink(missing_ok=True)
        raise RuntimeError(f"checksum mismatch {archive}: {actual} != {expected}")

    raw = pd.read_csv(archive, compression="zip")
    if not set(COLUMNS).issubset(raw.columns):
        raw = pd.read_csv(archive, compression="zip", header=None)
        if raw.shape[1] != len(COLUMNS):
            raise RuntimeError(f"unexpected metrics schema: {list(raw.columns)}")
        raw.columns = COLUMNS
        if str(raw.iloc[0]["create_time"]).strip().lower() == "create_time":
            raw = raw.iloc[1:].copy()
    for column in COLUMNS[2:]:
        raw[column] = pd.to_numeric(raw[column], errors="coerce")
    raw["metric_time"] = _parse_time(raw["create_time"])
    raw = raw.dropna(subset=["metric_time", *COLUMNS[2:]])
    return raw[["metric_time", *COLUMNS[2:]]].sort_values("metric_time")


def load_range_metrics(symbol: str, start: date, end: date, cache: Path) -> pd.DataFrame:
    frames = []
    day = start
    while day <= end:
        frames.append(load_day_metrics(symbol, day, cache))
        day += timedelta(days=1)
    frame = pd.concat(frames, ignore_index=True).sort_values("metric_time")
    frame = frame.drop_duplicates("metric_time", keep="last")
    expected_days = (end - start).days + 1
    if len(frame) < expected_days * 260:
        raise RuntimeError(f"incomplete five-minute metrics for {symbol}: {len(frame)}")
    return frame


def metric_features(raw: pd.DataFrame) -> pd.DataFrame:
    frame = raw.set_index("metric_time").sort_index().copy()
    # The public timestamp semantics are not documented as event-available time.
    # Shift one native sample so a decision at t uses only a value published at t-5m or earlier.
    base = frame.shift(1)
    out = pd.DataFrame(index=frame.index)
    oi = base["sum_open_interest_value"].where(base["sum_open_interest_value"] > 0)
    log_oi = np.log(oi)
    for lag in (1, 3, 6, 12, 24, 72):
        out[f"metric_oi_log_change_{lag}"] = log_oi.diff(lag)
    oi_med = oi.shift(1).rolling(288, min_periods=72).median()
    oi_mad = (oi.shift(1) - oi_med).abs().rolling(288, min_periods=72).median()
    out["metric_oi_robust_z_1d"] = (oi - oi_med) / (1.4826 * oi_mad + 1e-12)

    ratio_columns = {
        "top_account": "count_toptrader_long_short_ratio",
        "top_position": "sum_toptrader_long_short_ratio",
        "all_account": "count_long_short_ratio",
        "taker": "sum_taker_long_short_vol_ratio",
    }
    logs: dict[str, pd.Series] = {}
    for short, column in ratio_columns.items():
        value = base[column].where(base[column] > 0)
        logs[short] = np.log(value)
        out[f"metric_{short}_log"] = logs[short]
        for lag in (1, 3, 6, 12, 24):
            out[f"metric_{short}_change_{lag}"] = logs[short].diff(lag)
    out["metric_top_position_minus_account"] = logs["top_position"] - logs["top_account"]
    out["metric_top_minus_all_account"] = logs["top_account"] - logs["all_account"]
    out["metric_top_position_minus_all"] = logs["top_position"] - logs["all_account"]
    return out
