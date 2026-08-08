"""Causal higher-timeframe regime router for v142 competing auctions.

A strong, directionally efficient four-hour auction aligned with the original
excursion permits only the continuation route.  Otherwise only the rotation
route is permitted.  The strength threshold is an expanding prior-only
quantile; the current event cannot enter its own threshold.  Execution remains
entirely in NautilusTrader through the v53 runner.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any, Mapping

import numpy as np
import pandas as pd

from v53_nt_core import CostConfig, RotationSignal
from v107_regime_rotation_core import _prior_q
from v142_dual_auction_core import (
    DualAuctionConfig,
    build_rotation_signals as _build_dual_signals,
    build_state as _build_dual_state,
)


@dataclass(frozen=True, slots=True)
class TrendRouterConfig(DualAuctionConfig):
    trend_lookback_5m: int = 48
    trend_efficiency_quantile: float = 0.65

    @classmethod
    def from_mapping(cls, values: Mapping[str, Any]) -> "TrendRouterConfig":
        data = dict(values)
        data["auction_windows_5m"] = tuple(int(value) for value in data["auction_windows_5m"])
        allowed = set(cls.__dataclass_fields__)
        unknown = sorted(set(data) - allowed)
        if unknown:
            raise ValueError(f"unknown v145 config keys: {unknown}")
        return cls(**data)

    def __post_init__(self) -> None:
        DualAuctionConfig.__post_init__(self)
        if self.trend_lookback_5m < 12:
            raise ValueError("v145 trend lookback must be at least one hour")
        if not 0 < self.trend_efficiency_quantile < 1:
            raise ValueError("v145 trend efficiency quantile must be in (0,1)")


def build_state(features: pd.DataFrame, config: TrendRouterConfig) -> pd.DataFrame:
    state = _build_dual_state(features, config)
    bars = config.trend_lookback_5m
    path = state["close"].diff().abs().rolling(bars, min_periods=bars).sum()
    change = state["close"] - state["close"].shift(bars)
    efficiency = change.abs() / path.replace(0.0, np.nan)
    state["v145_trend_change"] = change
    state["v145_trend_efficiency"] = efficiency
    state["v145_trend_efficiency_q"] = _prior_q(
        efficiency,
        days=config.prior_quantile_days,
        q=config.trend_efficiency_quantile,
        minimum=config.prior_quantile_min_rows,
    )
    return state


def build_rotation_signals(
    *,
    state: pd.DataFrame,
    raw: pd.DataFrame,
    evaluation_start: pd.Timestamp,
    evaluation_end: pd.Timestamp,
    config: TrendRouterConfig,
    costs: CostConfig,
) -> list[RotationSignal]:
    signals = _build_dual_signals(
        state=state,
        raw=raw,
        evaluation_start=evaluation_start,
        evaluation_end=evaluation_end,
        config=config,
        costs=costs,
    )
    result: list[RotationSignal] = []
    for signal in signals:
        feature_time = pd.Timestamp(signal.source_feature_open_time_ns, unit="ns", tz="UTC")
        if feature_time not in state.index:
            continue
        row = state.loc[feature_time]
        change = float(row.get("v145_trend_change", math.nan))
        efficiency = float(row.get("v145_trend_efficiency", math.nan))
        threshold = float(row.get("v145_trend_efficiency_q", math.nan))
        if not all(math.isfinite(value) for value in (change, efficiency, threshold)):
            continue
        trend_direction = int(np.sign(change))
        original_rotation_side = str(signal.details["original_rotation_side"])
        excursion_direction = 1 if original_rotation_side == "SELL" else -1
        strong_aligned_trend = efficiency >= threshold and trend_direction == excursion_direction
        route = str(signal.details["competition_result"])
        allowed = (
            route == "CONTINUATION" and strong_aligned_trend
        ) or (
            route == "ROTATION" and not strong_aligned_trend
        )
        if not allowed:
            continue
        signal.details.update({
            "v145_trend_lookback_5m":config.trend_lookback_5m,
            "v145_trend_change":change,
            "v145_trend_direction":trend_direction,
            "v145_excursion_direction":excursion_direction,
            "v145_trend_efficiency":efficiency,
            "v145_trend_efficiency_threshold":threshold,
            "v145_strong_aligned_trend":strong_aligned_trend,
            "v145_route_allowed":True,
        })
        result.append(signal)
    return result
