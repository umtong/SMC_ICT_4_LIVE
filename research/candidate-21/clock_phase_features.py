"""Causal quarter-hour feature enrichment for Candidate 21.

Candidate 05 already downloads verified Binance aggTrades and builds minute
features.  This module reuses those archives and adds only the missing opening
10-second price response plus a lagged same-clock baseline.  It does not create
orders, fills, positions, PnL, or a backtest engine.
"""
from __future__ import annotations

from pathlib import Path
from typing import Iterable
import math

import numpy as np
import pandas as pd

try:
    from features import AGG_COLUMNS as _AGG_COLUMNS
except ModuleNotFoundError:  # Pure-unit-test fallback; production reuses Candidate 05.
    _AGG_COLUMNS = [
        "agg_trade_id",
        "price",
        "quantity",
        "first_trade_id",
        "last_trade_id",
        "transact_time",
        "is_buyer_maker",
    ]
AGG_COLUMNS = list(_AGG_COLUMNS)


def _agg_reader(path: Path, chunksize: int = 500_000) -> Iterable[pd.DataFrame]:
    probe = pd.read_csv(path, compression="zip", nrows=1)
    if set(AGG_COLUMNS).issubset(probe.columns):
        return pd.read_csv(
            path,
            compression="zip",
            usecols=AGG_COLUMNS,
            chunksize=chunksize,
        )
    return pd.read_csv(
        path,
        compression="zip",
        header=None,
        names=AGG_COLUMNS,
        usecols=range(len(AGG_COLUMNS)),
        chunksize=chunksize,
    )


def _maker_mask(series: pd.Series) -> pd.Series:
    if series.dtype == bool:
        return series
    return series.astype(str).str.strip().str.lower().isin(
        {"true", "1", "t", "yes"},
    )


def build_opening_windows(paths: list[Path]) -> pd.DataFrame:
    """Aggregate the first ten seconds of each minute from actual aggTrades."""
    pieces: list[pd.DataFrame] = []
    agg_paths = sorted(
        path
        for path in paths
        if path.suffix == ".zip" and "-aggTrades-" in path.name
    )
    if not agg_paths:
        raise ValueError("no Binance aggTrades archives were supplied")

    for path in agg_paths:
        for chunk in _agg_reader(path):
            price = pd.to_numeric(chunk["price"], errors="raise").astype(float)
            quantity = pd.to_numeric(chunk["quantity"], errors="raise").astype(float)
            transact = pd.to_numeric(chunk["transact_time"], errors="raise").astype("int64")
            unit = "us" if int(transact.iloc[0]) > 10**14 else "ms"
            timestamp = pd.to_datetime(transact, unit=unit, utc=True)
            opening_mask = timestamp.dt.second.lt(10)
            if not bool(opening_mask.any()):
                continue
            timestamp = timestamp[opening_mask]
            price = price[opening_mask]
            quantity = quantity[opening_mask]
            maker = _maker_mask(chunk.loc[opening_mask, "is_buyer_maker"])
            notional = price * quantity
            signed = np.where(maker.to_numpy(), -notional.to_numpy(), notional.to_numpy())
            work = pd.DataFrame(
                {
                    "minute": timestamp.dt.floor("min").to_numpy(),
                    "timestamp": timestamp.to_numpy(),
                    "price": price.to_numpy(),
                    "notional": notional.to_numpy(),
                    "signed_notional": signed,
                },
            ).sort_values("timestamp")
            grouped = work.groupby("minute", sort=True).agg(
                first_ts=("timestamp", "first"),
                last_ts=("timestamp", "last"),
                qh_open_price=("price", "first"),
                qh_open_high=("price", "max"),
                qh_open_low=("price", "min"),
                qh_open_close=("price", "last"),
                qh_open_notional=("notional", "sum"),
                qh_open_signed_notional=("signed_notional", "sum"),
                qh_open_trade_count=("price", "size"),
            )
            pieces.append(grouped.reset_index())

    if not pieces:
        raise ValueError("aggTrades contained no opening ten-second observations")
    combined = pd.concat(pieces, ignore_index=True)
    combined = combined.sort_values(["minute", "first_ts"])
    opening = combined.groupby("minute", sort=True).agg(
        qh_open_price=("qh_open_price", "first"),
        qh_open_high=("qh_open_high", "max"),
        qh_open_low=("qh_open_low", "min"),
        qh_open_close=("qh_open_close", "last"),
        qh_open_notional=("qh_open_notional", "sum"),
        qh_open_signed_notional=("qh_open_signed_notional", "sum"),
        qh_open_trade_count=("qh_open_trade_count", "sum"),
    )
    opening.index = pd.DatetimeIndex(opening.index)
    if opening.index.tz is None:
        opening.index = opening.index.tz_localize("UTC")
    else:
        opening.index = opening.index.tz_convert("UTC")
    opening["qh_open_return_bps"] = (
        np.log(opening["qh_open_close"] / opening["qh_open_price"]) * 10_000.0
    )
    opening["qh_open_range_bps"] = (
        np.log(opening["qh_open_high"] / opening["qh_open_low"]) * 10_000.0
    )
    opening["qh_open_impact_efficiency"] = (
        opening["qh_open_return_bps"].abs()
        / opening["qh_open_range_bps"].replace(0.0, np.nan)
    ).clip(0.0, 1.0)
    opening["qh_open_flow"] = (
        opening["qh_open_signed_notional"]
        / opening["qh_open_notional"].replace(0.0, np.nan)
    )
    if opening.index.duplicated().any() or not opening.index.is_monotonic_increasing:
        raise RuntimeError("opening-window timestamps must be unique and monotonic")
    return opening


def enrich_clock_features(
    features: pd.DataFrame,
    opening: pd.DataFrame,
    *,
    period_minutes: int = 15,
    baseline_periods: int = 96,
    min_baseline_samples: int = 32,
) -> pd.DataFrame:
    """Join opening response and normalize only against prior boundary windows."""
    if period_minutes < 1 or 60 % period_minutes != 0:
        raise ValueError("period_minutes must be a positive divisor of 60")
    if baseline_periods < 1:
        raise ValueError("baseline_periods must be positive")
    if not 1 <= min_baseline_samples <= baseline_periods:
        raise ValueError("min_baseline_samples must be within baseline_periods")
    if "observed_time_ns" not in features:
        raise ValueError("feature frame lacks observed_time_ns")

    result = features.copy()
    observed = pd.to_numeric(result["observed_time_ns"], errors="raise").astype("int64")
    if observed.duplicated().any() or not observed.is_monotonic_increasing:
        raise ValueError("feature observation timestamps must be unique and monotonic")
    minute = pd.to_datetime(observed, unit="ns", utc=True).dt.floor("min")
    result["_clock_minute"] = minute

    opening_frame = opening.copy()
    opening_frame.index.name = "_clock_minute"
    opening_frame = opening_frame.reset_index()
    result = result.merge(opening_frame, on="_clock_minute", how="left", validate="one_to_one")

    boundary = result["_clock_minute"].dt.minute.mod(period_minutes).eq(0)
    result["qh_boundary"] = boundary
    result["qh_phase_sample_count"] = 0.0
    result["qh_open_notional_baseline"] = np.nan
    result["qh_open_notional_burst"] = np.nan

    boundary_rows = result.loc[boundary, ["_clock_minute", "qh_open_notional"]].copy()
    series = pd.to_numeric(boundary_rows["qh_open_notional"], errors="coerce")
    lagged = series.shift(1)
    baseline = lagged.rolling(
        baseline_periods,
        min_periods=1,
    ).median()
    sample_count = lagged.rolling(
        baseline_periods,
        min_periods=1,
    ).count()
    burst = series / baseline.replace(0.0, np.nan)
    result.loc[boundary, "qh_phase_sample_count"] = sample_count.to_numpy(dtype=float)
    result.loc[boundary, "qh_open_notional_baseline"] = baseline.to_numpy(dtype=float)
    result.loc[boundary, "qh_open_notional_burst"] = burst.to_numpy(dtype=float)

    finite_fields = [
        "qh_open_flow",
        "qh_open_return_bps",
        "qh_open_range_bps",
        "qh_open_impact_efficiency",
        "qh_open_notional_burst",
    ]
    finite = np.ones(len(result), dtype=bool)
    for name in finite_fields:
        values = pd.to_numeric(result[name], errors="coerce").to_numpy(dtype=float)
        finite &= np.isfinite(values)
    result["qh_feature_ready"] = (
        boundary.to_numpy(dtype=bool)
        & finite
        & result["qh_phase_sample_count"].to_numpy(dtype=float).__ge__(
            float(min_baseline_samples),
        )
    )
    result = result.drop(columns=["_clock_minute"])
    if result["observed_time_ns"].duplicated().any():
        raise RuntimeError("clock enrichment duplicated feature timestamps")
    return result


def augment_feature_file(
    *,
    feature_path: Path,
    raw_files: list[Path],
    destination: Path,
    period_minutes: int,
    baseline_periods: int,
    min_baseline_samples: int,
) -> Path:
    features = pd.read_csv(feature_path, compression="infer")
    opening = build_opening_windows(raw_files)
    enriched = enrich_clock_features(
        features,
        opening,
        period_minutes=period_minutes,
        baseline_periods=baseline_periods,
        min_baseline_samples=min_baseline_samples,
    )
    destination.parent.mkdir(parents=True, exist_ok=True)
    enriched.to_csv(destination, index=False, compression="gzip")
    return destination


__all__ = [
    "augment_feature_file",
    "build_opening_windows",
    "enrich_clock_features",
]
