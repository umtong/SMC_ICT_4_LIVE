"""Transient-versus-resilient quarter-hour impact for candidate-02 v78.

The module converts completed market observations into deterministic intents only.
NautilusTrader owns orders, fills, positions, fees, and account NAV.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any, Mapping

import numpy as np
import pandas as pd

from v53_nt_core import CostConfig, RotationSignal, cost_after_reward_risk
from v75_quarter_hour_core import QuarterHourConfig, build_state as build_quarter_hour_state

NS_MINUTE = 60_000_000_000
UTC = "UTC"


@dataclass(frozen=True, slots=True)
class ImpactResilienceConfig(QuarterHourConfig):
    accepted_range_minutes: int = 30
    retention_threshold: float = 0.65
    minimum_displacement_atr: float = 0.20
    maximum_displacement_atr: float = 3.00
    stop_buffer_atr: float = 0.15
    target_range_multiple: float = 1.00
    minimum_rest_flow_alignment: float = 0.0
    minimum_minute_flow_alignment: float = 0.10
    depth_transition_threshold: float = 0.0

    @classmethod
    def from_mapping(cls, values: Mapping[str, Any]) -> "ImpactResilienceConfig":
        allowed = set(cls.__dataclass_fields__)
        unknown = sorted(set(values) - allowed)
        if unknown:
            raise ValueError(f"unknown v78 config keys: {unknown}")
        return cls(**dict(values))

    def __post_init__(self) -> None:
        if self.mode not in {"RESILIENT_CONTINUATION", "TRANSIENT_FAILURE_ROTATION"}:
            raise ValueError("invalid v78 mode")
        if self.auction_minutes != 15:
            raise ValueError("v78 origin must remain the quarter-hour auction")
        if self.prior_days < 2 or self.prior_minimum_events < 32:
            raise ValueError("insufficient prospective threshold history")
        for name in (
            "opening_flow_abs_quantile",
            "opening_turnover_quantile",
            "opening_abs_return_quantile",
            "opening_roundness_quantile",
        ):
            value = float(getattr(self, name))
            if not 0.0 < value < 1.0:
                raise ValueError(f"invalid {name}")
        if self.accepted_range_minutes not in {15, 30, 60}:
            raise ValueError("accepted_range_minutes must be 15, 30, or 60")
        if not 0.0 < self.retention_threshold < 1.25:
            raise ValueError("invalid retention threshold")
        if not 0.0 < self.minimum_displacement_atr < self.maximum_displacement_atr:
            raise ValueError("invalid displacement ATR band")
        if not 0.0 <= self.stop_buffer_atr <= 1.0:
            raise ValueError("invalid stop buffer")
        if not 0.25 <= self.target_range_multiple <= 2.0:
            raise ValueError("invalid target range multiple")
        if not -1.0 < self.minimum_rest_flow_alignment < 1.0:
            raise ValueError("invalid rest flow threshold")
        if not -1.0 < self.minimum_minute_flow_alignment < 1.0:
            raise ValueError("invalid minute flow threshold")
        if self.maximum_holding_minutes <= 0:
            raise ValueError("maximum holding time must be positive")
        if not 0.0 < self.minimum_cost_after_rr <= self.maximum_cost_after_rr:
            raise ValueError("invalid cost-after RR band")


def build_state(features: pd.DataFrame, config: ImpactResilienceConfig) -> pd.DataFrame:
    return build_quarter_hour_state(features, config)


def _finite(row: pd.Series, names: tuple[str, ...]) -> bool:
    return all(name in row.index and math.isfinite(float(row[name])) for name in names)


def _atr(raw: pd.DataFrame, lookback: int) -> pd.Series:
    previous = raw["close"].shift(1)
    true_range = pd.concat(
        [
            raw["high"] - raw["low"],
            (raw["high"] - previous).abs(),
            (raw["low"] - previous).abs(),
        ],
        axis=1,
    ).max(axis=1)
    return true_range.shift(1).rolling(lookback, min_periods=max(20, lookback // 2)).mean()


def build_rotation_signals(
    *,
    state: pd.DataFrame,
    raw: pd.DataFrame,
    evaluation_start: pd.Timestamp,
    evaluation_end: pd.Timestamp,
    config: ImpactResilienceConfig,
    costs: CostConfig,
) -> list[RotationSignal]:
    start = pd.Timestamp(evaluation_start)
    end = pd.Timestamp(evaluation_end)
    start = start.tz_localize(UTC) if start.tzinfo is None else start.tz_convert(UTC)
    end = end.tz_localize(UTC) if end.tzinfo is None else end.tz_convert(UTC)
    if end <= start:
        raise ValueError("evaluation end must be after start")

    atr = _atr(raw, config.atr_lookback_minutes)
    candidates = state.loc[
        (state.index >= start)
        & (state.index < end - pd.Timedelta(minutes=1))
        & (state["is_quarter_hour_open"] > 0.5)
    ]
    required = (
        "opening_abs_flow",
        "opening_flow_threshold",
        "opening_turnover",
        "opening_turnover_threshold",
        "opening_abs_return",
        "opening_return_threshold",
        "opening_price_alignment",
        "minute_price_alignment",
        "minute_flow_alignment",
        "rest_flow_alignment",
        "front_depth_change",
    )

    signals: list[RotationSignal] = []
    cooldown_until = -1
    for ts, row in candidates.iterrows():
        setup_ns = int(ts.value)
        confirmation_ts = ts + pd.Timedelta(minutes=1)
        confirmation_ns = int(confirmation_ts.value)
        if confirmation_ns <= cooldown_until or not _finite(row, required):
            continue
        if ts not in raw.index or confirmation_ts not in raw.index or ts not in atr.index:
            continue
        direction = int(np.sign(float(row["opening_direction"])))
        if direction == 0:
            continue
        if float(row["opening_abs_flow"]) < max(config.minimum_opening_flow_ratio, float(row["opening_flow_threshold"])):
            continue
        if float(row["opening_turnover"]) < float(row["opening_turnover_threshold"]):
            continue
        if float(row["opening_abs_return"]) < float(row["opening_return_threshold"]):
            continue
        if float(row["opening_price_alignment"]) <= 0.0 or float(row["minute_price_alignment"]) <= 0.0:
            continue
        if float(row["minute_flow_alignment"]) < config.minimum_minute_flow_alignment:
            continue
        if float(row["rest_flow_alignment"]) <= config.minimum_rest_flow_alignment:
            continue

        first = raw.loc[ts]
        second = raw.loc[confirmation_ts]
        volatility = float(atr.loc[ts])
        origin = float(first["open"])
        first_close = float(first["close"])
        second_close = float(second["close"])
        displacement = direction * (first_close - origin)
        if not all(math.isfinite(v) for v in (volatility, origin, first_close, second_close, displacement)):
            continue
        if volatility <= 0.0 or displacement <= 0.0:
            continue
        displacement_atr = displacement / volatility
        if not config.minimum_displacement_atr <= displacement_atr <= config.maximum_displacement_atr:
            continue

        retention = direction * (second_close - origin) / displacement
        formation = raw.loc[
            (raw.index >= ts - pd.Timedelta(minutes=config.accepted_range_minutes))
            & (raw.index <= ts - pd.Timedelta(minutes=1))
        ]
        if len(formation) < config.accepted_range_minutes - 1:
            continue
        accepted_high = float(formation["close"].max())
        accepted_low = float(formation["close"].min())
        accepted_width = accepted_high - accepted_low
        if not math.isfinite(accepted_width) or accepted_width <= 0.0:
            continue

        front_change = float(row["front_depth_change"])
        first_high = float(first["high"])
        first_low = float(first["low"])
        second_high = float(second["high"])
        second_low = float(second["low"])

        if config.mode == "RESILIENT_CONTINUATION":
            if front_change >= config.depth_transition_threshold:
                continue
            if retention < config.retention_threshold:
                continue
            if direction * (second_close - origin) <= 0.0:
                continue
            side = "BUY" if direction > 0 else "SELL"
            entry = second_close
            stop = origin - direction * config.stop_buffer_atr * volatility
            target = entry + direction * config.target_range_multiple * accepted_width
            transition = "impact_retained_with_front_liquidity_withdrawal"
        else:
            if front_change < config.depth_transition_threshold:
                continue
            if retention > config.retention_threshold:
                continue
            # A transient impact must lose at least half of the opening displacement
            # before the reversal is considered observable.
            if direction * (second_close - first_close) >= 0.0:
                continue
            side = "SELL" if direction > 0 else "BUY"
            entry = second_close
            extreme = max(first_high, second_high) if direction > 0 else min(first_low, second_low)
            stop = extreme + direction * config.stop_buffer_atr * volatility
            target = accepted_low if direction > 0 else accepted_high
            transition = "impact_decayed_with_front_liquidity_replenishment"

        geometry = stop < entry < target if side == "BUY" else target < entry < stop
        if not geometry:
            continue
        rr = cost_after_reward_risk(entry=entry, stop=stop, target=target, side=side, costs=costs)
        if not math.isfinite(rr) or not config.minimum_cost_after_rr <= rr <= config.maximum_cost_after_rr:
            continue

        turnover_ratio = float(row["opening_turnover"]) / max(float(row["opening_turnover_threshold"]), 1e-12)
        score = float(row["opening_abs_flow"]) * max(turnover_ratio, 1.0) * (1.0 + abs(front_change))
        details = {
            "mode": config.mode,
            "transition": transition,
            "quarter_hour_open_utc": (ts - pd.Timedelta(minutes=1)).isoformat(),
            "confirmation_time_utc": confirmation_ts.isoformat(),
            "accepted_range_minutes": config.accepted_range_minutes,
            "accepted_close_high": accepted_high,
            "accepted_close_low": accepted_low,
            "accepted_close_width": accepted_width,
            "opening_displacement": displacement,
            "opening_displacement_atr": displacement_atr,
            "second_minute_retention": retention,
            "retention_threshold": config.retention_threshold,
            "front_depth_change": front_change,
            "entry_order_type": "MARKET",
            "confirmation": "second_completed_minute_price_resilience_after_direct_quarter_hour_flow",
        }
        signals.append(
            RotationSignal(
                scenario_id=f"v78-{config.mode.lower()}-{confirmation_ns}",
                observed_time_ns=confirmation_ns,
                side=side,
                entry_reference=entry,
                stop_price=stop,
                target_price=target,
                cost_after_reward_risk=rr,
                score=score,
                max_hold_minutes=config.maximum_holding_minutes,
                source_feature_open_time_ns=setup_ns - NS_MINUTE,
                source_feature_available_time_ns=setup_ns,
                source_max_market_time_ns=confirmation_ns,
                details=details,
            )
        )
        cooldown_until = confirmation_ns + config.cooldown_minutes * NS_MINUTE

    for signal in signals:
        if signal.source_max_market_time_ns > signal.observed_time_ns:
            raise AssertionError("future information detected")
    return signals


__all__ = ["ImpactResilienceConfig", "build_state", "build_rotation_signals"]
