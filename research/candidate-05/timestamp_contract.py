"""Strict observation-time conversion across pandas versions.

pandas 3 can preserve millisecond or microsecond datetime resolution and its
integer conversion then returns values in that preserved unit.  Candidate 05
uses nanoseconds at every Nautilus boundary, so exchange epochs are converted
through integer dtype and completed-kline observation timestamps are rebuilt
explicitly from ``Timestamp.value`` (always Unix nanoseconds).

This module changes observational timestamps only.  It contains no signal,
execution, fill, accounting, or PnL logic.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd

import features as _features


_ORIGINAL_BUILD_FEATURES = _features.build_features


def numeric_epoch(values: pd.Series) -> pd.Series:
    numeric = pd.to_numeric(values, errors="raise")
    if numeric.isna().any():
        raise RuntimeError("timestamp column contains missing values")
    if numeric.dtype.kind == "f":
        fractional = numeric % 1.0
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


def normalize_epoch_ns(values: pd.Series) -> pd.Series:
    """Return integer Unix nanoseconds regardless of the source epoch unit."""
    numeric = numeric_epoch(values)
    factor = {"s": 1_000_000_000, "ms": 1_000_000, "us": 1_000, "ns": 1}[
        timestamp_unit(numeric)
    ]
    maximum = int(numeric.abs().max())
    if maximum > (2**63 - 1) // factor:
        raise RuntimeError("timestamp overflows int64 nanoseconds")
    result = (numeric * factor).astype("int64")
    converted = pd.to_datetime(result, unit="ns", utc=True)
    if converted.min() < pd.Timestamp("2010-01-01", tz="UTC"):
        raise RuntimeError("timestamp normalization produced an implausibly old observation")
    if converted.max() > pd.Timestamp("2100-01-01", tz="UTC"):
        raise RuntimeError("timestamp normalization produced an implausibly future observation")
    return result


def epoch_datetime(values: pd.Series) -> pd.Series:
    numeric = numeric_epoch(values)
    return pd.to_datetime(numeric, unit=timestamp_unit(numeric), utc=True)


def datetime_values_ns(values: pd.Series) -> pd.Series:
    """Convert datetime-like values to explicit Unix nanoseconds.

    ``Timestamp.value`` is defined in nanoseconds even when the parent pandas
    Series retains a lower datetime resolution.
    """
    result = pd.Series(
        (pd.Timestamp(value).value for value in values),
        index=values.index,
        dtype="int64",
    )
    if result.empty or result.duplicated().any() or not result.is_monotonic_increasing:
        raise RuntimeError("completed-kline observation timestamps must be unique and monotonic")
    return result


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


def build_features(klines: pd.DataFrame, *args: Any, **kwargs: Any) -> pd.DataFrame:
    """Build features, then bind every row to its exact completed-bar ns time."""
    result = _ORIGINAL_BUILD_FEATURES(klines, *args, **kwargs)
    if len(result) != len(klines):
        raise RuntimeError("feature rows do not match completed kline rows")
    observed = datetime_values_ns(klines["close_time_dt"].reset_index(drop=True))
    result = result.copy()
    result["observed_time_ns"] = observed.to_numpy(copy=True)
    if result["observed_time_ns"].duplicated().any() or not result["observed_time_ns"].is_monotonic_increasing:
        raise RuntimeError("feature observation timestamps are not unique and monotonic nanoseconds")
    return result


def install() -> None:
    _features.read_kline = read_kline
    _features.build_features = build_features
