"""Causal Binance spot participation enrichment for the leverage-flow router.

The existing futures feature builder remains authoritative for perpetual bars,
aggressor flow, displayed depth, positioning, and premium-index observations.
This wrapper adds checksum-verified Binance spot klines and aggregate trades,
then joins only completed-minute observations. It never matches orders,
constructs fills, maintains positions, or computes PnL.
"""
from __future__ import annotations

from dataclasses import asdict
from datetime import date, timedelta
from functools import wraps
import json
from pathlib import Path
import urllib.request
from typing import Any, Callable

import numpy as np
import pandas as pd

import features as _features

_BASE_LOAD_RANGE: Callable[..., Any] | None = None
SPOT_BASE = "https://data.binance.vision/data/spot/daily"
NS_PER_MINUTE = 60_000_000_000


def _datetime_ns(values: pd.Series | pd.Index) -> np.ndarray:
    """Return explicit Unix nanoseconds independent of pandas datetime storage unit."""
    converted = pd.to_datetime(values, utc=True)
    return pd.DatetimeIndex(converted).to_numpy(dtype="datetime64[ns]").astype("int64")


def _spot_archive_spec(endpoint: str, symbol: str, day: date) -> tuple[str, str]:
    stamp = day.isoformat()
    if endpoint == "klines":
        relative = f"klines/{symbol}/1m/{symbol}-1m-{stamp}.zip"
    elif endpoint == "aggTrades":
        relative = f"aggTrades/{symbol}/{symbol}-aggTrades-{stamp}.zip"
    else:
        raise ValueError(f"unsupported spot endpoint: {endpoint}")
    return f"{SPOT_BASE}/{relative}", Path(relative).name


def _download_spot_checked(
    endpoint: str,
    symbol: str,
    day: date,
    cache: Path,
) -> tuple[Path, Path, Any]:
    url, filename = _spot_archive_spec(endpoint, symbol, day)
    directory = cache / "spot" / endpoint
    directory.mkdir(parents=True, exist_ok=True)
    archive = directory / filename
    checksum = directory / f"{filename}.CHECKSUM"
    if not archive.exists():
        urllib.request.urlretrieve(url, archive)
    if not checksum.exists():
        urllib.request.urlretrieve(url + ".CHECKSUM", checksum)
    expected = checksum.read_text(encoding="utf-8").strip().split()[0].lower()
    actual = _features.sha256_file(archive)
    if actual != expected:
        raise RuntimeError(
            f"checksum mismatch for spot archive {archive}: {actual} != {expected}",
        )
    evidence = _features.RawEvidence(
        endpoint=f"spot_{endpoint}",
        day=day.isoformat(),
        archive=str(archive),
        checksum=str(checksum),
        size_bytes=archive.stat().st_size,
        sha256=actual,
    )
    return archive, checksum, evidence


def _combine_agg(frames: list[pd.DataFrame]) -> pd.DataFrame:
    result = pd.concat(frames).sort_index()
    if not result.index.duplicated().any():
        return result
    return result.groupby(level=0, sort=True).agg(
        trade_open=("trade_open", "first"),
        trade_high=("trade_high", "max"),
        trade_low=("trade_low", "min"),
        trade_close=("trade_close", "last"),
        quantity_60s=("quantity_60s", "sum"),
        notional_60s=("notional_60s", "sum"),
        signed_notional_60s=("signed_notional_60s", "sum"),
        buy_notional_60s=("buy_notional_60s", "sum"),
        sell_notional_60s=("sell_notional_60s", "sum"),
        trade_count_60s=("trade_count_60s", "sum"),
        path_60s_bps=("path_60s_bps", "sum"),
        notional_15s=("notional_15s", "sum"),
        signed_notional_15s=("signed_notional_15s", "sum"),
        trade_count_15s=("trade_count_15s", "sum"),
        path_15s_bps=("path_15s_bps", "sum"),
        notional_open_10s=("notional_open_10s", "sum"),
        signed_notional_open_10s=("signed_notional_open_10s", "sum"),
        trade_count_open_10s=("trade_count_open_10s", "sum"),
    )


def _spot_features(klines: pd.DataFrame, agg: pd.DataFrame) -> pd.DataFrame:
    frame = klines.set_index("open_time_dt").copy()
    frame = frame.join(agg, how="left")
    frame["spot_open"] = pd.to_numeric(frame["open"], errors="raise")
    frame["spot_high"] = pd.to_numeric(frame["high"], errors="raise")
    frame["spot_low"] = pd.to_numeric(frame["low"], errors="raise")
    frame["spot_close"] = pd.to_numeric(frame["close"], errors="raise")
    frame["spot_quote_volume"] = pd.to_numeric(frame["quote_volume"], errors="raise")

    notional = frame["notional_60s"].replace(0.0, np.nan)
    frame["spot_flow_60s"] = frame["signed_notional_60s"] / notional
    frame["spot_flow_3m"] = (
        frame["signed_notional_60s"].rolling(3, min_periods=3).sum()
        / frame["notional_60s"].rolling(3, min_periods=3).sum().replace(0.0, np.nan)
    )
    frame["spot_trade_vwap_60s"] = (
        frame["notional_60s"] / frame["quantity_60s"].replace(0.0, np.nan)
    )
    frame["spot_ret_1m_bps"] = (
        np.log(frame["trade_close"] / frame["trade_open"]) * 10_000.0
    )
    frame["spot_ret_5m_bps"] = (
        np.log(frame["spot_close"] / frame["spot_close"].shift(5)) * 10_000.0
    )
    frame["spot_efficiency_60s"] = (
        frame["spot_ret_1m_bps"].abs()
        / frame["path_60s_bps"].replace(0.0, np.nan)
    ).clip(0.0, 1.0)
    past_notional = frame["notional_60s"].shift(1).rolling(120, min_periods=60).median()
    frame["spot_notional_burst"] = (
        frame["notional_60s"] / past_notional.replace(0.0, np.nan)
    )
    frame["spot_prior_15m_high"] = (
        frame["spot_high"].shift(1).rolling(15, min_periods=15).max()
    )
    frame["spot_prior_15m_low"] = (
        frame["spot_low"].shift(1).rolling(15, min_periods=15).min()
    )
    frame["spot_observed_time_ns"] = _datetime_ns(frame["close_time_dt"])
    frame["minute_start_ns"] = _datetime_ns(frame.index)

    required = [
        "spot_open",
        "spot_high",
        "spot_low",
        "spot_close",
        "spot_flow_60s",
        "spot_flow_3m",
        "spot_trade_vwap_60s",
        "spot_ret_1m_bps",
        "spot_ret_5m_bps",
        "spot_efficiency_60s",
        "spot_notional_burst",
        "spot_prior_15m_high",
        "spot_prior_15m_low",
    ]
    frame["spot_feature_ready"] = frame[required].notna().all(axis=1)
    columns = [
        "minute_start_ns",
        "spot_observed_time_ns",
        "spot_feature_ready",
        "spot_open",
        "spot_high",
        "spot_low",
        "spot_close",
        "spot_quote_volume",
        "spot_flow_60s",
        "spot_flow_3m",
        "spot_trade_vwap_60s",
        "spot_ret_1m_bps",
        "spot_ret_5m_bps",
        "spot_efficiency_60s",
        "spot_notional_burst",
        "spot_prior_15m_high",
        "spot_prior_15m_low",
    ]
    result = frame[columns].reset_index(drop=True)
    if result["minute_start_ns"].duplicated().any():
        raise RuntimeError("duplicate completed spot minutes")
    return result


def _as_bool(series: pd.Series) -> pd.Series:
    if series.dtype == bool:
        return series
    return series.astype(str).str.strip().str.lower().isin({"true", "1", "yes"})


def load_range(
    *,
    symbol: str,
    start: date,
    end: date,
    cache: Path,
    output: Path,
):
    if _BASE_LOAD_RANGE is None:
        raise RuntimeError("spot participation contract was not installed")
    klines, feature_path, manifest_files, evidence = _BASE_LOAD_RANGE(
        symbol=symbol,
        start=start,
        end=end,
        cache=cache,
        output=output,
    )

    spot_kline_frames: list[pd.DataFrame] = []
    spot_agg_frames: list[pd.DataFrame] = []
    day = start
    while day <= end:
        kline_path, kline_checksum, kline_evidence = _download_spot_checked(
            "klines", symbol, day, cache,
        )
        agg_path, agg_checksum, agg_evidence = _download_spot_checked(
            "aggTrades", symbol, day, cache,
        )
        spot_kline_frames.append(_features.read_kline(kline_path))
        spot_agg_frames.append(_features.aggregate_agg_trades(agg_path))
        # Do not expose spot aggTrades through the inherited raw_files list:
        # Candidate 20/21 use that list to build the perpetual execution clock.
        # Spot provenance is retained in raw_evidence.json instead.
        evidence.extend([kline_evidence, agg_evidence])
        day += timedelta(days=1)

    spot_klines = pd.concat(spot_kline_frames, ignore_index=True).sort_values(
        "close_time_dt",
    )
    if spot_klines["close_time_dt"].duplicated().any():
        raise RuntimeError("duplicate spot klines across daily files")
    expected_days = (end - start).days + 1
    if len(spot_klines) < expected_days * 1_430:
        raise RuntimeError(
            f"incomplete spot minute data: {len(spot_klines)} rows for {expected_days} days",
        )
    spot = _spot_features(spot_klines, _combine_agg(spot_agg_frames))

    base = pd.read_csv(feature_path, compression="infer")
    observed = pd.to_numeric(base["observed_time_ns"], errors="raise").astype("int64")
    base["minute_start_ns"] = observed // NS_PER_MINUTE * NS_PER_MINUTE
    merged = base.merge(
        spot,
        on="minute_start_ns",
        how="left",
        validate="one_to_one",
        sort=False,
    )
    future_spot = (
        pd.to_numeric(merged["spot_observed_time_ns"], errors="coerce")
        > pd.to_numeric(merged["observed_time_ns"], errors="raise")
    )
    if future_spot.fillna(False).any():
        raise RuntimeError("future spot observation reached perpetual feature row")

    merged["perp_spot_basis_bps"] = (
        merged["trade_vwap_60s"]
        / merged["spot_trade_vwap_60s"].replace(0.0, np.nan)
        - 1.0
    ) * 10_000.0
    merged["perp_spot_basis_change_1m_bps"] = merged["perp_spot_basis_bps"].diff(1)
    merged["perp_spot_basis_change_5m_bps"] = merged["perp_spot_basis_bps"].diff(5)
    merged["spot_minus_perp_ret_1m_bps"] = (
        merged["spot_ret_1m_bps"] - merged["ret_60s_bps"]
    )
    merged["spot_minus_perp_flow_60s"] = (
        merged["spot_flow_60s"] - merged["flow_60s"]
    )
    merged["spot_perp_notional_ratio"] = (
        merged["spot_quote_volume"] / merged["notional_60s"].replace(0.0, np.nan)
    )

    spot_ready = _as_bool(merged["spot_feature_ready"].fillna(False))
    base_ready = _as_bool(merged["feature_ready"])
    derived = [
        "perp_spot_basis_bps",
        "perp_spot_basis_change_1m_bps",
        "perp_spot_basis_change_5m_bps",
        "spot_minus_perp_ret_1m_bps",
        "spot_minus_perp_flow_60s",
        "spot_perp_notional_ratio",
    ]
    merged["spot_participation_ready"] = spot_ready & merged[derived].notna().all(axis=1)
    merged["feature_ready"] = base_ready & merged["spot_participation_ready"]
    merged = merged.drop(columns=["minute_start_ns"])
    if merged["observed_time_ns"].duplicated().any():
        raise RuntimeError("spot participation join duplicated observations")
    merged.to_csv(feature_path, index=False, compression="gzip")
    (output / "raw_evidence.json").write_text(
        json.dumps([asdict(item) for item in evidence], indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return klines, feature_path, manifest_files, evidence


def install() -> None:
    """Wrap the currently installed causal feature loader exactly once."""
    global _BASE_LOAD_RANGE
    current = _features.load_range
    if getattr(current, "_external_spot_participation_contract", False):
        return
    _BASE_LOAD_RANGE = current
    wrapped = wraps(current)(load_range)
    setattr(wrapped, "_external_spot_participation_contract", True)
    _features.load_range = wrapped


__all__ = ["SPOT_BASE", "install", "load_range"]
