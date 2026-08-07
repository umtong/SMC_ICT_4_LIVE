"""Five-second causal flow bars and protected-swing helpers."""
from __future__ import annotations

from dataclasses import replace
from typing import Iterable

import numpy as np
import pandas as pd

import diagnose_impact_resilience_1s as impact
import run_local_liquidity_sweep_mss_retest as local


NS_PER_SECOND = 1_000_000_000
NS_PER_FIVE_SECONDS = 5 * NS_PER_SECOND
NS_PER_FIFTEEN_SECONDS = 15 * NS_PER_SECOND


def scaled_execution_logic(logic: local.LocalSweepMSSLogic) -> local.LocalSweepMSSLogic:
    """Keep every economic wall-clock window unchanged on five-second bars."""
    return replace(
        logic,
        atr_history_bars=logic.atr_history_bars * 3,
        reference_history_bars=logic.reference_history_bars * 3,
        mss_context_bars=logic.mss_context_bars * 3,
        maximum_mss_bars=logic.maximum_mss_bars * 3,
        maximum_retest_bars=logic.maximum_retest_bars * 3,
    )


def prepare_five_second_bars(
    seconds: pd.DataFrame,
    logic: local.LocalSweepMSSLogic,
) -> pd.DataFrame:
    """Build complete causal five-second flow bars with prior-only references."""
    logic.validate()
    required = {
        "timestamp_ns",
        "open",
        "high",
        "low",
        "close",
        "volume",
        "quote_volume",
        "taker_buy_quote",
        "taker_sell_quote",
    }
    missing = required.difference(seconds.columns)
    if missing:
        raise ValueError(f"second columns missing: {sorted(missing)}")
    work = seconds.copy().sort_values("timestamp_ns", kind="stable").reset_index(drop=True)
    work["timestamp_ns"] = work["timestamp_ns"].astype("int64")
    work["bucket_5s"] = work["timestamp_ns"] // NS_PER_FIVE_SECONDS
    grouped = work.groupby("bucket_5s", sort=True)
    bars = grouped.agg(
        timestamp_ns=("timestamp_ns", "last"),
        open=("open", "first"),
        high=("high", "max"),
        low=("low", "min"),
        close=("close", "last"),
        volume=("volume", "sum"),
        quote_volume=("quote_volume", "sum"),
        taker_buy_quote=("taker_buy_quote", "sum"),
        taker_sell_quote=("taker_sell_quote", "sum"),
        second_count=("timestamp_ns", "count"),
    ).reset_index(drop=True)
    bars = bars[bars["second_count"] == 5].copy().reset_index(drop=True)
    bars["signed_quote"] = bars["taker_buy_quote"] - bars["taker_sell_quote"]
    bars["imbalance"] = (
        bars["signed_quote"] / bars["quote_volume"].replace(0.0, np.nan)
    ).fillna(0.0)
    bars["vwap"] = (
        bars["quote_volume"] / bars["volume"].replace(0.0, np.nan)
    ).fillna(bars["close"])
    bars["range"] = bars["high"] - bars["low"]
    bars["price_efficiency"] = (
        (bars["close"] - bars["open"]).abs()
        / bars["range"].replace(0.0, np.nan)
    ).fillna(0.0)

    previous = bars["close"].shift(1)
    true_range = pd.concat(
        [
            bars["range"],
            (bars["high"] - previous).abs(),
            (bars["low"] - previous).abs(),
        ],
        axis=1,
    ).max(axis=1)
    bars["atr"] = true_range.shift(1).rolling(
        logic.atr_history_bars,
        min_periods=logic.atr_history_bars,
    ).median()
    bars["signed_flow_reference"] = bars["signed_quote"].abs().shift(1).rolling(
        logic.reference_history_bars,
        min_periods=logic.reference_history_bars,
    ).quantile(logic.attack_signed_flow_quantile)
    bars["quote_volume_reference"] = bars["quote_volume"].shift(1).rolling(
        logic.reference_history_bars,
        min_periods=logic.reference_history_bars,
    ).quantile(logic.attack_quote_volume_quantile)
    bars["imbalance_reference"] = bars["imbalance"].abs().shift(1).rolling(
        logic.reference_history_bars,
        min_periods=logic.reference_history_bars,
    ).quantile(logic.attack_imbalance_quantile)
    bars["body"] = bars["close"] - bars["open"]
    bars["body_atr"] = bars["body"].abs() / bars["atr"].replace(0.0, np.nan)
    bars["body_reference"] = bars["body_atr"].shift(1).rolling(
        logic.reference_history_bars,
        min_periods=logic.reference_history_bars,
    ).quantile(logic.displacement_body_quantile)
    bars["close_location"] = (
        (bars["close"] - bars["low"])
        / bars["range"].replace(0.0, np.nan)
    ).fillna(0.5)
    return bars


def latest_five_second_boundary(
    pools: Iterable[impact.Pool],
    *,
    direction: str,
    event_start_ns: int,
    event_close: float,
    source_pivot_ns: int,
    context_ns: int,
) -> impact.Pool | None:
    """Return latest protected 5S swing known before the sweep bar began."""
    side = "UPPER" if direction == "LONG" else "LOWER"
    earliest_ns = event_start_ns - context_ns
    eligible = [
        pool
        for pool in pools
        if pool.side == side
        and int(pool.pivot_ts_ns) != int(source_pivot_ns)
        and earliest_ns <= int(pool.confirmed_ts_ns) < event_start_ns
        and (pool.level > event_close if direction == "LONG" else pool.level < event_close)
    ]
    if not eligible:
        return None
    return max(
        eligible,
        key=lambda pool: (
            int(pool.confirmed_ts_ns),
            -abs(pool.level - event_close),
        ),
    )


def entry_second_index(timestamps: np.ndarray, observed_ns: int) -> int | None:
    index = int(np.searchsorted(timestamps, int(observed_ns), side="left"))
    return None if index >= len(timestamps) else index


_prepare_five_second_bars = prepare_five_second_bars
_latest_five_second_boundary = latest_five_second_boundary
