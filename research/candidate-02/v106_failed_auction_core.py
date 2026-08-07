"""Causal rolling-auction failure signals for candidate-02 v106.

This module creates trade intents only. NautilusTrader remains the sole owner
of orders, fills, positions, fees and NAV.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any, Mapping

import numpy as np
import pandas as pd

from v53_nt_core import (
    NS_MINUTE,
    CostConfig,
    RotationConfig,
    RotationSignal,
    _true_range,
    build_state as _v53_build_state,
    cost_after_reward_risk,
)

UTC = "UTC"


@dataclass(frozen=True, slots=True)
class FailedAuctionConfig(RotationConfig):
    auction_windows_5m: tuple[int, ...] = (9, 12, 15)
    sweep_buffer_atr: float = 0.05
    reclaim_depth_atr: float = 0.0
    minimum_auction_range_atr: float = 2.0
    maximum_center_drift_fraction: float = 0.35

    @classmethod
    def from_mapping(cls, values: Mapping[str, Any]) -> "FailedAuctionConfig":
        data = dict(values)
        data["auction_windows_5m"] = tuple(int(value) for value in data["auction_windows_5m"])
        allowed = set(cls.__dataclass_fields__)
        unknown = sorted(set(data) - allowed)
        if unknown:
            raise ValueError(f"unknown v106 config keys: {unknown}")
        return cls(**data)

    def __post_init__(self) -> None:
        super().__post_init__()
        if not self.auction_windows_5m or any(value < 6 for value in self.auction_windows_5m):
            raise ValueError("v106 auction windows must contain at least six 5-minute bars")
        if tuple(sorted(set(self.auction_windows_5m))) != self.auction_windows_5m:
            raise ValueError("v106 auction windows must be unique and increasing")
        if self.sweep_buffer_atr < 0 or self.reclaim_depth_atr < 0:
            raise ValueError("v106 sweep and reclaim buffers cannot be negative")
        if self.minimum_auction_range_atr <= 0:
            raise ValueError("v106 minimum auction range must be positive")
        if not 0 <= self.maximum_center_drift_fraction <= 1:
            raise ValueError("v106 center drift fraction must be in [0, 1]")


def build_state(features: pd.DataFrame, config: FailedAuctionConfig) -> pd.DataFrame:
    # Reuse the already audited prior-only quantile machinery. The actual
    # auction edges and center are built from completed one-minute bars below.
    return _v53_build_state(features, config)


def _normalize_time(value: pd.Timestamp) -> pd.Timestamp:
    value = pd.Timestamp(value)
    return value.tz_localize(UTC) if value.tzinfo is None else value.tz_convert(UTC)


def _weighted_center(frame: pd.DataFrame) -> float:
    weights = frame["volume"].clip(lower=0.0)
    total = float(weights.sum())
    if total <= 0:
        return float(frame["close"].mean())
    return float((frame["close"] * weights).sum() / total)


def _five_minute_closes(frame: pd.DataFrame, bars: int) -> pd.Series:
    expected = bars * 5
    if len(frame) != expected:
        return pd.Series(dtype="float64")
    values = frame["close"].to_numpy(dtype="float64", copy=False)
    return pd.Series(values[4::5], dtype="float64")


def build_rotation_signals(
    *,
    state: pd.DataFrame,
    raw: pd.DataFrame,
    evaluation_start: pd.Timestamp,
    evaluation_end: pd.Timestamp,
    config: FailedAuctionConfig,
    costs: CostConfig,
) -> list[RotationSignal]:
    start = _normalize_time(evaluation_start)
    end = _normalize_time(evaluation_end)
    if end <= start:
        raise ValueError("evaluation_end must be after evaluation_start")

    tr = _true_range(raw)
    atr = tr.rolling(
        config.atr_lookback_minutes,
        min_periods=max(30, config.atr_lookback_minutes // 2),
    ).median()
    rows = state.loc[start - pd.Timedelta(days=2):end]
    selected: dict[int, RotationSignal] = {}

    for feature_open_time, row in rows.iterrows():
        observed_time = feature_open_time + pd.Timedelta(minutes=5)
        if not start <= observed_time < end or observed_time not in raw.index:
            continue
        if not (
            math.isfinite(float(row.get("effq", math.nan)))
            and math.isfinite(float(row.get("vpinq", math.nan)))
            and math.isfinite(float(row.get("abs_oiq", math.nan)))
            and float(row["vpin_50"]) <= float(row["vpinq"])
            and abs(float(row["oi_change_1h"])) <= float(row["abs_oiq"])
        ):
            continue
        atr_value = float(atr.asof(observed_time))
        if not math.isfinite(atr_value) or atr_value <= 0:
            continue
        current = raw.loc[(raw.index > feature_open_time) & (raw.index <= observed_time)]
        if len(current) != 5:
            continue
        current_open = float(current.iloc[0]["open"])
        current_close = float(current.iloc[-1]["close"])
        current_high = float(current["high"].max())
        current_low = float(current["low"].min())
        flow = 2.0 * float(row["taker_buy_ratio_5m"]) - 1.0
        depth = float(row["depth_imbalance_1pct"])

        candidates: list[RotationSignal] = []
        for bars in config.auction_windows_5m:
            auction_start = feature_open_time - pd.Timedelta(minutes=bars * 5)
            prior = raw.loc[(raw.index > auction_start) & (raw.index <= feature_open_time)]
            closes = _five_minute_closes(prior, bars)
            if len(closes) != bars:
                continue
            path = float(closes.diff().abs().sum())
            efficiency = abs(float(closes.iloc[-1] - closes.iloc[0])) / path if path > 0 else 0.0
            if efficiency > float(row["effq"]):
                continue
            prior_high = float(prior["high"].max())
            prior_low = float(prior["low"].min())
            auction_range = prior_high - prior_low
            if auction_range < config.minimum_auction_range_atr * atr_value:
                continue
            half = len(prior) // 2
            first_center = _weighted_center(prior.iloc[:half])
            second_center = _weighted_center(prior.iloc[half:])
            center_drift = abs(second_center - first_center) / auction_range
            if center_drift > config.maximum_center_drift_fraction:
                continue
            center = _weighted_center(prior)
            sweep = config.sweep_buffer_atr * atr_value
            reclaim = config.reclaim_depth_atr * atr_value
            upper = current_high >= prior_high + sweep and current_close <= prior_high - reclaim
            lower = current_low <= prior_low - sweep and current_close >= prior_low + reclaim
            if upper == lower:
                continue
            if upper:
                side = "SELL"
                if not (current_close < current_open and flow <= 0 and depth <= 0):
                    continue
                stop = current_high + config.stop_buffer_atr * atr_value
                target = center
                geometry = target < current_close < stop
                excursion = current_high - prior_high
            else:
                side = "BUY"
                if not (current_close > current_open and flow >= 0 and depth >= 0):
                    continue
                stop = current_low - config.stop_buffer_atr * atr_value
                target = center
                geometry = stop < current_close < target
                excursion = prior_low - current_low
            if not geometry:
                continue
            rr = cost_after_reward_risk(
                entry=current_close,
                stop=stop,
                target=target,
                side=side,
                costs=costs,
            )
            if not math.isfinite(rr) or not (
                config.minimum_cost_after_rr <= rr <= config.maximum_cost_after_rr
            ):
                continue
            observed_ns = int(observed_time.value)
            feature_ns = int(feature_open_time.value)
            score = rr + excursion / atr_value + (1.0 - center_drift)
            candidates.append(
                RotationSignal(
                    scenario_id=f"v106-failed-auction-{bars}-{observed_ns}",
                    observed_time_ns=observed_ns,
                    side=side,
                    entry_reference=current_close,
                    stop_price=stop,
                    target_price=target,
                    cost_after_reward_risk=rr,
                    score=score,
                    max_hold_minutes=config.maximum_holding_minutes,
                    source_feature_open_time_ns=feature_ns,
                    source_feature_available_time_ns=observed_ns,
                    source_max_market_time_ns=observed_ns,
                    details={
                        "auction_window_minutes": bars * 5,
                        "auction_high": prior_high,
                        "auction_low": prior_low,
                        "auction_center": center,
                        "auction_efficiency": efficiency,
                        "efficiency_threshold": float(row["effq"]),
                        "center_drift_fraction": center_drift,
                        "vpin": float(row["vpin_50"]),
                        "vpin_threshold": float(row["vpinq"]),
                        "oi_change_1h": float(row["oi_change_1h"]),
                        "oi_abs_threshold": float(row["abs_oiq"]),
                        "confirmation_flow": flow,
                        "confirmation_depth": depth,
                        "atr_1m": atr_value,
                        "sweep_excursion_atr": excursion / atr_value,
                    },
                )
            )
        if candidates:
            best = max(candidates, key=lambda value: (value.score, -value.details["auction_window_minutes"]))
            selected[best.observed_time_ns] = best

    signals = sorted(selected.values(), key=lambda value: value.observed_time_ns)
    for signal in signals:
        if signal.source_max_market_time_ns > signal.observed_time_ns:
            raise AssertionError("future information detected in v106")
        if signal.observed_time_ns != signal.source_feature_open_time_ns + 5 * NS_MINUTE:
            raise AssertionError("v106 feature availability mismatch")
    return signals
