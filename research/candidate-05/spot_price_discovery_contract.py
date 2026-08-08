"""Checksum-verified Binance spot observations for price-discovery routing.

The contract appends spot trade flow and price-path features to the existing
USD-M perpetual feature file.  Spot observations become usable only at the
completed one-minute close.  It never matches orders, creates fills, sizes risk,
maintains positions or calculates account PnL.
"""
from __future__ import annotations

from dataclasses import asdict
from datetime import date, timedelta
from functools import wraps
import json
import math
from pathlib import Path
import urllib.request
from typing import Any, Callable

import numpy as np
import pandas as pd

import features as _features


_SPOT_BASE = "https://data.binance.vision/data/spot/daily"
_BASE_LOAD_RANGE: Callable[..., Any] | None = None


def _spot_archive_spec(endpoint: str, symbol: str, day: date) -> tuple[str, str]:
    stamp = day.isoformat()
    if endpoint == "klines":
        filename = f"{symbol}-1m-{stamp}.zip"
        url = f"{_SPOT_BASE}/klines/{symbol}/1m/{filename}"
    elif endpoint == "aggTrades":
        filename = f"{symbol}-aggTrades-{stamp}.zip"
        url = f"{_SPOT_BASE}/aggTrades/{symbol}/{filename}"
    else:
        raise ValueError(f"unsupported spot endpoint: {endpoint}")
    return url, filename


def _download_spot_checked(
    endpoint: str,
    symbol: str,
    day: date,
    cache: Path,
):
    url, filename = _spot_archive_spec(endpoint, symbol, day)
    directory = Path(cache) / f"spot_{endpoint}"
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
        raise RuntimeError(f"checksum mismatch for {archive}: {actual} != {expected}")
    evidence = _features.RawEvidence(
        endpoint=f"spot_{endpoint}",
        day=day.isoformat(),
        archive=str(archive),
        checksum=str(checksum),
        size_bytes=archive.stat().st_size,
        sha256=actual,
    )
    return archive, checksum, evidence


def _combine_agg_frames(frames: list[pd.DataFrame]) -> pd.DataFrame:
    aggregate = pd.concat(frames).sort_index()
    if not aggregate.index.duplicated().any():
        return aggregate
    return aggregate.groupby(level=0, sort=True).agg(
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


def build_spot_features(
    klines: pd.DataFrame,
    agg: pd.DataFrame,
) -> pd.DataFrame:
    """Build completed-minute spot flow and price-path observations."""
    frame = klines.set_index("open_time_dt").copy().join(agg, how="left")
    denominator = frame["notional_60s"].replace(0.0, np.nan)
    tail_denominator = frame["notional_15s"].replace(0.0, np.nan)
    frame["spot_flow_60s"] = frame["signed_notional_60s"] / denominator
    frame["spot_flow_15s"] = frame["signed_notional_15s"] / tail_denominator
    frame["spot_flow_15s"] = frame["spot_flow_15s"].fillna(frame["spot_flow_60s"])
    frame["spot_flow_3m"] = (
        frame["signed_notional_60s"].rolling(3, min_periods=3).sum()
        / frame["notional_60s"].rolling(3, min_periods=3).sum().replace(0.0, np.nan)
    )
    frame["spot_trade_vwap_60s"] = (
        frame["notional_60s"] / frame["quantity_60s"].replace(0.0, np.nan)
    )
    frame["spot_trade_close"] = frame["trade_close"].fillna(frame["close"])
    frame["spot_ret_60s_bps"] = (
        np.log(frame["trade_close"] / frame["trade_open"]) * 10_000.0
    )
    frame["spot_efficiency_60s"] = (
        frame["spot_ret_60s_bps"].abs()
        / frame["path_60s_bps"].replace(0.0, np.nan)
    ).clip(0.0, 1.0)
    prior_median = frame["notional_60s"].shift(1).rolling(120, min_periods=60).median()
    frame["spot_notional_burst"] = (
        frame["notional_60s"] / prior_median.replace(0.0, np.nan)
    )
    frame["spot_observed_time"] = pd.to_datetime(
        frame["close_time_dt"],
        utc=True,
        errors="raise",
    ).astype("datetime64[ns, UTC]")
    result = frame[
        [
            "spot_observed_time",
            "spot_trade_close",
            "spot_trade_vwap_60s",
            "spot_flow_15s",
            "spot_flow_60s",
            "spot_flow_3m",
            "spot_ret_60s_bps",
            "spot_efficiency_60s",
            "spot_notional_burst",
        ]
    ].sort_values("spot_observed_time")
    if result["spot_observed_time"].duplicated().any():
        raise RuntimeError("duplicate spot observation timestamps")
    return result


def load_range(
    *,
    symbol: str,
    start: date,
    end: date,
    cache: Path,
    output: Path,
):
    if _BASE_LOAD_RANGE is None:
        raise RuntimeError("spot price-discovery contract was not installed")
    klines, feature_path, manifest_files, evidence = _BASE_LOAD_RANGE(
        symbol=symbol,
        start=start,
        end=end,
        cache=cache,
        output=output,
    )

    spot_klines: list[pd.DataFrame] = []
    spot_agg: list[pd.DataFrame] = []
    day = start
    while day <= end:
        kline_path, kline_checksum, kline_evidence = _download_spot_checked(
            "klines",
            symbol,
            day,
            cache,
        )
        agg_path, agg_checksum, agg_evidence = _download_spot_checked(
            "aggTrades",
            symbol,
            day,
            cache,
        )
        spot_klines.append(_features.read_kline(kline_path))
        spot_agg.append(_features.aggregate_agg_trades(agg_path))
        manifest_files.extend(
            [kline_path, kline_checksum, agg_path, agg_checksum],
        )
        evidence.extend([kline_evidence, agg_evidence])
        day += timedelta(days=1)

    spot_kline_frame = pd.concat(spot_klines, ignore_index=True).sort_values(
        "close_time_dt",
    )
    if spot_kline_frame["close_time_dt"].duplicated().any():
        raise RuntimeError("duplicate spot klines across daily files")
    expected_days = (end - start).days + 1
    if len(spot_kline_frame) < expected_days * 1_430:
        raise RuntimeError(
            f"incomplete spot minute data: {len(spot_kline_frame)} rows for "
            f"{expected_days} days",
        )
    spot = build_spot_features(
        spot_kline_frame,
        _combine_agg_frames(spot_agg),
    )

    perpetual = pd.read_csv(feature_path, compression="infer")
    perpetual["feature_observed_time"] = pd.to_datetime(
        pd.to_numeric(perpetual["observed_time_ns"], errors="raise").astype("int64"),
        unit="ns",
        utc=True,
    ).astype("datetime64[ns, UTC]")
    if perpetual["feature_observed_time"].dtype != spot["spot_observed_time"].dtype:
        raise RuntimeError(
            "perpetual and spot observation timestamps have different resolutions: "
            f"{perpetual['feature_observed_time'].dtype} != "
            f"{spot['spot_observed_time'].dtype}",
        )
    joined = pd.merge_asof(
        perpetual.sort_values("feature_observed_time"),
        spot.sort_values("spot_observed_time"),
        left_on="feature_observed_time",
        right_on="spot_observed_time",
        direction="backward",
        allow_exact_matches=True,
    )
    joined["spot_age_seconds"] = (
        joined["feature_observed_time"] - joined["spot_observed_time"]
    ).dt.total_seconds()
    if joined["spot_age_seconds"].dropna().lt(0.0).any():
        raise RuntimeError("future spot observation reached perpetual feature rows")
    required = [
        "spot_trade_close",
        "spot_flow_15s",
        "spot_flow_60s",
        "spot_flow_3m",
        "spot_ret_60s_bps",
        "spot_efficiency_60s",
        "spot_notional_burst",
    ]
    joined["spot_ready"] = (
        joined[required].notna().all(axis=1)
        & joined["spot_age_seconds"].ge(0.0)
        & joined["spot_age_seconds"].le(65.0)
    )
    joined["perp_minus_spot_return_bps"] = (
        joined["ret_60s_bps"] - joined["spot_ret_60s_bps"]
    )
    joined["perp_spot_basis_bps"] = np.log(
        joined["trade_vwap_60s"] / joined["spot_trade_vwap_60s"],
    ) * 10_000.0
    joined = joined.drop(columns=["feature_observed_time", "spot_observed_time"])
    joined.to_csv(feature_path, index=False, compression="gzip")
    (output / "raw_evidence.json").write_text(
        json.dumps([asdict(item) for item in evidence], indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return klines, feature_path, manifest_files, evidence


def install() -> None:
    """Wrap the currently installed loader without changing execution logic."""
    global _BASE_LOAD_RANGE
    current = _features.load_range
    if getattr(current, "_candidate05_spot_price_discovery_contract", False):
        return
    _BASE_LOAD_RANGE = current
    wrapped = wraps(current)(load_range)
    setattr(wrapped, "_candidate05_spot_price_discovery_contract", True)
    _features.load_range = wrapped


__all__ = [
    "build_spot_features",
    "install",
]
