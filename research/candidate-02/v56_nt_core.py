"""High-impact order-flow price-discovery signals for candidate-02 v56.

This module emits causal intents only. NautilusTrader owns execution, fees,
positions and NAV.
"""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any, Mapping

import numpy as np
import pandas as pd

from v53_nt_core import CostConfig, RotationSignal, cost_after_reward_risk

NS_MINUTE = 60_000_000_000


@dataclass(frozen=True, slots=True)
class PriceDiscoveryConfig:
    flow_lookback_bars_5m: int = 3
    prior_quantile_days: int = 30
    prior_quantile_min_rows: int = 1000
    flow_abs_quantile: float = 0.70
    impact_quantile: float = 0.55
    minimum_event_response: float = 0.0
    minimum_confirmation_return: float = 0.0
    minimum_confirmation_depth: float = -0.10
    minimum_oi_change_1h: float = 0.0
    atr_lookback_minutes: int = 60
    stop_retracement_fraction: float = 0.50
    stop_buffer_atr: float = 0.15
    target_impulse_multiple: float = 2.0
    maximum_holding_minutes: int = 120
    cooldown_bars: int = 6
    minimum_cost_after_rr: float = 1.0
    maximum_cost_after_rr: float = 3.0

    @classmethod
    def from_mapping(cls, values: Mapping[str, Any]) -> "PriceDiscoveryConfig":
        allowed = set(cls.__dataclass_fields__)
        unknown = sorted(set(values) - allowed)
        if unknown:
            raise ValueError(f"unknown v56 config keys: {unknown}")
        return cls(**dict(values))

    def __post_init__(self) -> None:
        if self.flow_lookback_bars_5m < 2:
            raise ValueError("flow lookback must be at least two bars")
        if self.prior_quantile_days <= 0 or self.prior_quantile_min_rows <= 0:
            raise ValueError("prior history must be positive")
        for name in ("flow_abs_quantile", "impact_quantile"):
            value = getattr(self, name)
            if not 0 < value < 1:
                raise ValueError(f"{name} must be in (0, 1)")
        if not 0 < self.stop_retracement_fraction < 1:
            raise ValueError("stop retracement fraction must be in (0, 1)")
        if self.stop_buffer_atr < 0 or self.target_impulse_multiple <= 0:
            raise ValueError("invalid stop or target geometry")
        if not 0 < self.minimum_cost_after_rr <= self.maximum_cost_after_rr:
            raise ValueError("invalid cost-after reward/risk bounds")


def _prior_quantile(series: pd.Series, *, config: PriceDiscoveryConfig, quantile: float) -> pd.Series:
    return series.shift(1).rolling(
        config.prior_quantile_days * 288,
        min_periods=config.prior_quantile_min_rows,
    ).quantile(quantile)


def build_state(features: pd.DataFrame, config: PriceDiscoveryConfig) -> pd.DataFrame:
    x = features.copy()
    n = config.flow_lookback_bars_5m
    signed_flow = 2.0 * x["taker_buy_ratio_5m"] - 1.0
    weight = x["vol_5m"].clip(lower=1e-12)
    x["episode_flow"] = (
        (signed_flow * weight).rolling(n, min_periods=n).sum()
        / weight.rolling(n, min_periods=n).sum()
    )
    x["episode_flow_abs"] = x["episode_flow"].abs()
    x["episode_direction"] = np.sign(x["episode_flow"])
    x["episode_origin_close"] = x["close"].shift(n)
    x["episode_displacement"] = x["close"] - x["episode_origin_close"]
    x["episode_log_return"] = np.log(x["close"] / x["episode_origin_close"])
    x["episode_response"] = x["episode_direction"] * x["episode_log_return"]
    x["episode_impact"] = (
        x["episode_response"] / x["episode_flow_abs"].replace(0.0, np.nan)
    )
    x["flow_abs_threshold"] = _prior_quantile(
        x["episode_flow_abs"],
        config=config,
        quantile=config.flow_abs_quantile,
    )
    x["impact_threshold"] = _prior_quantile(
        x["episode_impact"].where(x["episode_impact"] > 0.0),
        config=config,
        quantile=config.impact_quantile,
    )

    # At row t the episode is the previous fully completed 15-minute state.
    x["event_direction"] = x["episode_direction"].shift(1)
    x["event_flow_abs"] = x["episode_flow_abs"].shift(1)
    x["event_response"] = x["episode_response"].shift(1)
    x["event_impact"] = x["episode_impact"].shift(1)
    x["event_flow_threshold"] = x["flow_abs_threshold"].shift(1)
    x["event_impact_threshold"] = x["impact_threshold"].shift(1)
    x["event_origin_close"] = x["close"].shift(n + 1)
    x["event_end_close"] = x["close"].shift(1)
    x["confirmation_return"] = x["event_direction"] * x["log_ret_5m"]
    x["confirmation_depth"] = x["event_direction"] * x["depth_imbalance_1pct"]
    x["eligible_price_discovery"] = (
        (x["event_flow_abs"] >= x["event_flow_threshold"])
        & (x["event_response"] > config.minimum_event_response)
        & (x["event_impact"] >= x["event_impact_threshold"])
        & (x["confirmation_return"] > config.minimum_confirmation_return)
        & (x["confirmation_depth"] >= config.minimum_confirmation_depth)
        & (x["oi_change_1h"] > config.minimum_oi_change_1h)
    )
    return x


def _true_range(raw: pd.DataFrame) -> pd.Series:
    previous_close = raw["close"].shift(1)
    return pd.concat(
        [
            raw["high"] - raw["low"],
            (raw["high"] - previous_close).abs(),
            (raw["low"] - previous_close).abs(),
        ],
        axis=1,
    ).max(axis=1)


def _utc(value: pd.Timestamp) -> pd.Timestamp:
    result = pd.Timestamp(value)
    return result.tz_localize("UTC") if result.tzinfo is None else result.tz_convert("UTC")


def build_rotation_signals(
    *,
    state: pd.DataFrame,
    raw: pd.DataFrame,
    evaluation_start: pd.Timestamp,
    evaluation_end: pd.Timestamp,
    config: PriceDiscoveryConfig,
    costs: CostConfig,
) -> list[RotationSignal]:
    start = _utc(evaluation_start)
    end = _utc(evaluation_end)
    atr = _true_range(raw).rolling(
        config.atr_lookback_minutes,
        min_periods=max(30, config.atr_lookback_minutes // 2),
    ).median()
    candidates = state.loc[state["eligible_price_discovery"].fillna(False)]
    signals: list[RotationSignal] = []
    cooldown_until = pd.Timestamp.min.tz_localize("UTC")
    for feature_open, row in candidates.iterrows():
        observed_time = feature_open + pd.Timedelta(minutes=5)
        if not start <= observed_time < end:
            continue
        if observed_time <= cooldown_until or observed_time not in raw.index:
            continue
        direction = int(np.sign(float(row["event_direction"])))
        if direction == 0:
            continue
        origin = float(row["event_origin_close"])
        event_end = float(row["event_end_close"])
        impulse = abs(event_end - origin)
        entry_reference = float(raw.at[observed_time, "close"])
        atr_value = float(atr.asof(observed_time))
        if not (
            math.isfinite(origin)
            and math.isfinite(event_end)
            and math.isfinite(impulse)
            and impulse > 0
            and math.isfinite(atr_value)
            and atr_value > 0
        ):
            continue
        midpoint = origin + config.stop_retracement_fraction * (event_end - origin)
        stop = midpoint - direction * config.stop_buffer_atr * atr_value
        target = entry_reference + direction * config.target_impulse_multiple * impulse
        side = "BUY" if direction > 0 else "SELL"
        geometry_valid = (
            stop < entry_reference < target
            if side == "BUY"
            else target < entry_reference < stop
        )
        if not geometry_valid:
            continue
        reward_risk = cost_after_reward_risk(
            entry=entry_reference,
            stop=stop,
            target=target,
            side=side,
            costs=costs,
        )
        if not math.isfinite(reward_risk) or not (
            config.minimum_cost_after_rr
            <= reward_risk
            <= config.maximum_cost_after_rr
        ):
            continue
        observed_ns = int(observed_time.value)
        feature_open_ns = int(feature_open.value)
        signals.append(
            RotationSignal(
                scenario_id=f"v56-price-discovery-{observed_ns}",
                observed_time_ns=observed_ns,
                side=side,
                entry_reference=entry_reference,
                stop_price=stop,
                target_price=target,
                cost_after_reward_risk=reward_risk,
                score=float(row["event_impact"] * row["event_flow_abs"]),
                max_hold_minutes=config.maximum_holding_minutes,
                source_feature_open_time_ns=feature_open_ns,
                source_feature_available_time_ns=observed_ns,
                source_max_market_time_ns=observed_ns,
                details={
                    "branch": "HIGH_IMPACT_PRICE_DISCOVERY",
                    "direction": direction,
                    "event_origin_close": origin,
                    "event_end_close": event_end,
                    "event_impulse": impulse,
                    "event_flow_abs": float(row["event_flow_abs"]),
                    "event_flow_threshold": float(row["event_flow_threshold"]),
                    "event_response": float(row["event_response"]),
                    "event_impact": float(row["event_impact"]),
                    "event_impact_threshold": float(row["event_impact_threshold"]),
                    "confirmation_return": float(row["confirmation_return"]),
                    "confirmation_depth": float(row["confirmation_depth"]),
                    "oi_change_1h": float(row["oi_change_1h"]),
                    "atr_1m": atr_value,
                    "stop_retracement_fraction": config.stop_retracement_fraction,
                    "target_impulse_multiple": config.target_impulse_multiple,
                },
            )
        )
        cooldown_until = observed_time + pd.Timedelta(minutes=5 * config.cooldown_bars)

    signals.sort(key=lambda item: item.observed_time_ns)
    if len({item.observed_time_ns for item in signals}) != len(signals):
        raise ValueError("duplicate v56 signal timestamps")
    for item in signals:
        if item.source_max_market_time_ns > item.observed_time_ns:
            raise AssertionError("future information detected")
        if item.source_feature_open_time_ns + 5 * NS_MINUTE != item.observed_time_ns:
            raise AssertionError("feature availability mismatch")
    return signals
