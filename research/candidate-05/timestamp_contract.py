"""Strict epoch conversion for Binance archives across pandas versions.

Pandas 3 treats object-dtype numeric strings passed with ``unit=`` as date
strings in some paths.  Candidate 05 therefore converts exchange epochs to
integer dtype before unit-aware conversion.  The installer replaces only the
observational kline reader; it does not affect execution or accounting.
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

import features as _features


def numeric_epoch(values: pd.Series) -> pd.Series:
    numeric = pd.to_numeric(values, errors="raise")
    if numeric.isna().any():
        raise RuntimeError("timestamp column contains missing values")
    fractional = numeric.astype("float64") % 1.0
    if (fractional != 0.0).any():
        raise RuntimeError("timestamp column contains non-integer epochs")
    return numeric.astype("int64")


def timestamp_unit(values: pd.Series) -> str:
    first = abs(int(values.iloc[0]))
    if first >= 10**17:
        return "ns"
    if first >= 10**14:
        return "us"
    if first >= 10**11:
        return "ms"
    if first >= 10**8:
        return "s"
    raise RuntimeError(f"unsupported epoch magnitude: {first}")


def epoch_datetime(values: pd.Series) -> pd.Series:
    numeric = numeric_epoch(values)
    return pd.to_datetime(numeric, unit=timestamp_unit(numeric), utc=True)


def read_kline(path: Path) -> pd.DataFrame:
    raw = pd.read_csv(path, compression="zip", header=None)
    columns = _features.KLINE_COLUMNS
    if raw.shape[1] == len(columns):
        raw.columns = columns
        first = str(raw.iloc[0]["open_time"])
        if not first.lstrip("-").isdigit():
            raw = raw.iloc[1:].copy()
    else:
        with_header = pd.read_csv(path, compression="zip")
        if not set(columns).issubset(with_header.columns):
            raise RuntimeError(f"unexpected kline schema in {path}: {list(with_header.columns)}")
        raw = with_header[columns].copy()

    for column in ("open", "high", "low", "close", "volume", "quote_volume"):
        raw[column] = pd.to_numeric(raw[column], errors="raise")
    raw["open_time_dt"] = epoch_datetime(raw["open_time"])
    raw["close_time_dt"] = epoch_datetime(raw["close_time"])
    frame = raw[
        ["open_time_dt", "close_time_dt", "open", "high", "low", "close", "volume", "quote_volume"]
    ].copy()
    frame = frame.sort_values("close_time_dt")
    if frame["close_time_dt"].duplicated().any():
        raise RuntimeError(f"duplicate kline close times in {path}")
    return frame


def install() -> None:
    _features.read_kline = read_kline
