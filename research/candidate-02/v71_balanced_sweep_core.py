"""Balanced-auction external-liquidity sweep and passive retest for v71.

A completed low-efficiency, low-toxicity auction must have repeatedly traded
both boundaries. Aggressive flow then sweeps one frozen external boundary, the
same-side book replenishes, and the completed minute closes back inside. A
post-only limit rests at the consumed boundary for five minutes; if filled, the
auction equilibrium is the target and the observed sweep extreme is the
scenario invalidation.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
import math
from typing import Any, Mapping

import numpy as np
import pandas as pd

from v53_nt_core import CostConfig, RotationSignal, cost_after_reward_risk

NS_MINUTE = 60_000_000_000
MODE = "BALANCED_AUCTION_SWEEP_PASSIVE_RETEST"


@dataclass(frozen=True, slots=True)
class BalancedSweepConfig:
    mode: str = MODE
    auction_minutes: int = 30
    prior_window_minutes: int = 720
    prior_minimum_minutes: int = 180
    flow_abs_quantile: float = 0.60
    impact_efficiency_low_quantile: float = 0.50
    vpin_quantile: float = 0.60
    flow_floor: float = 0.05
    maximum_path_efficiency: float = 0.35
    minimum_range_atr: float = 1.0
    maximum_range_atr: float = 6.0
    touch_tolerance_atr: float = 0.15
    minimum_touches_each_side: int = 2
    sweep_buffer_atr: float = 0.05
    reclaim_depth_atr: float = 0.05
    minimum_directional_flow: float = 0.10
    minimum_replenishment_fraction: float = 0.0
    stop_buffer_atr: float = 0.10
    entry_expiry_minutes: int = 5
    maximum_holding_minutes: int = 90
    cooldown_minutes: int = 5
    atr_lookback_minutes: int = 60
    minimum_cost_after_rr: float = 0.80
    maximum_cost_after_rr: float = 5.0

    @classmethod
    def from_mapping(cls, values: Mapping[str, Any]) -> "BalancedSweepConfig":
        unknown = sorted(set(values) - set(cls.__dataclass_fields__))
        if unknown:
            raise ValueError(f"unknown v71 config keys: {unknown}")
        return cls(**dict(values))

    def __post_init__(self) -> None:
        if self.mode != MODE:
            raise ValueError("v71 mode changed")
        if self.auction_minutes not in {20, 30, 45}:
            raise ValueError("auction horizon must be 20, 30 or 45 minutes")
        if self.prior_minimum_minutes >= self.prior_window_minutes:
            raise ValueError("prior minimum must be below prior window")
        for name in ("flow_abs_quantile", "impact_efficiency_low_quantile", "vpin_quantile"):
            if not 0 < float(getattr(self, name)) < 1:
                raise ValueError(f"{name} must be in (0,1)")
        if not 0 < self.maximum_path_efficiency < 1:
            raise ValueError("invalid path efficiency")
        if not 0 < self.minimum_range_atr < self.maximum_range_atr:
            raise ValueError("invalid range/ATR bounds")
        if self.minimum_touches_each_side < 2:
            raise ValueError("both sides need repeated touches")
        if self.entry_expiry_minutes <= 0 or self.maximum_holding_minutes <= 0:
            raise ValueError("invalid order lifetime")
        if not 0 < self.minimum_cost_after_rr <= self.maximum_cost_after_rr:
            raise ValueError("invalid cost-after RR band")


def _prior_quantile(series: pd.Series, config: BalancedSweepConfig, q: float) -> pd.Series:
    return series.shift(1).rolling(
        config.prior_window_minutes,
        min_periods=config.prior_minimum_minutes,
    ).quantile(q)


def build_state(features: pd.DataFrame, config: BalancedSweepConfig) -> pd.DataFrame:
    x = features.copy()
    one_return = np.log(x["close"] / x["close"].shift(1))
    x["impact_efficiency_1m"] = one_return.abs() / (
        x["signed_flow_ratio_1m"].abs() + config.flow_floor
    )
    x["flow_abs_threshold"] = _prior_quantile(
        x["signed_flow_ratio_1m"].abs(), config, config.flow_abs_quantile
    )
    x["impact_efficiency_low_threshold"] = _prior_quantile(
        x["impact_efficiency_1m"], config, config.impact_efficiency_low_quantile
    )
    x["vpin_threshold"] = _prior_quantile(x["vpin_50"], config, config.vpin_quantile)
    return x


def _atr(raw: pd.DataFrame, lookback: int) -> pd.Series:
    previous = raw["close"].shift(1)
    true_range = pd.concat(
        [
            (raw["high"] - raw["low"]).abs(),
            (raw["high"] - previous).abs(),
            (raw["low"] - previous).abs(),
        ],
        axis=1,
    ).max(axis=1)
    return true_range.rolling(lookback, min_periods=max(30, lookback // 2)).median()


def _passive_costs(costs: CostConfig) -> CostConfig:
    return CostConfig(
        entry_fee_rate=costs.target_fee_rate,
        target_fee_rate=costs.target_fee_rate,
        stop_fee_rate=costs.stop_fee_rate,
        entry_slippage_rate=Decimal("0"),
        stop_slippage_rate=costs.stop_slippage_rate,
        market_impact_rate=costs.market_impact_rate,
        funding_rate_allowance=costs.funding_rate_allowance,
    )


def build_rotation_signals(
    *,
    state: pd.DataFrame,
    raw: pd.DataFrame,
    evaluation_start: pd.Timestamp,
    evaluation_end: pd.Timestamp,
    config: BalancedSweepConfig,
    costs: CostConfig,
) -> list[RotationSignal]:
    start = pd.Timestamp(evaluation_start)
    end = pd.Timestamp(evaluation_end)
    if start.tz is None:
        start = start.tz_localize("UTC")
    if end.tz is None:
        end = end.tz_localize("UTC")
    x = state.join(
        raw[["high", "low", "open"]].rename(
            columns={"high": "high_1m", "low": "low_1m", "open": "open_1m"}
        ),
        how="inner",
    )
    atr_series = _atr(raw, config.atr_lookback_minutes)
    passive_costs = _passive_costs(costs)
    signals: list[RotationSignal] = []
    cooldown_until_ns = -1

    for i in range(config.auction_minutes, len(x)):
        observed_time = x.index[i]
        if observed_time < start or observed_time >= end:
            continue
        if int(observed_time.value) <= cooldown_until_ns:
            continue
        row = x.iloc[i]
        required = (
            "flow_abs_threshold",
            "impact_efficiency_1m",
            "impact_efficiency_low_threshold",
            "vpin_50",
            "vpin_threshold",
            "signed_flow_ratio_1m",
            "ask_depth_change_1m",
            "bid_depth_change_1m",
        )
        if any(not math.isfinite(float(row[name])) for name in required):
            continue
        formation = x.iloc[i - config.auction_minutes : i]
        if len(formation) != config.auction_minutes:
            continue
        atr_value = float(atr_series.asof(observed_time))
        if not math.isfinite(atr_value) or atr_value <= 0:
            continue
        high = float(formation["high_1m"].max())
        low = float(formation["low_1m"].min())
        width = high - low
        if not (
            config.minimum_range_atr * atr_value
            <= width
            <= config.maximum_range_atr * atr_value
        ):
            continue
        movement = abs(float(formation["close"].iloc[-1]) - float(formation["open_1m"].iloc[0]))
        path = float(formation["close"].diff().abs().sum())
        efficiency = movement / path if path > 0 else 0.0
        if efficiency > config.maximum_path_efficiency:
            continue
        tolerance = config.touch_tolerance_atr * atr_value
        upper_touches = int((formation["high_1m"] >= high - tolerance).sum())
        lower_touches = int((formation["low_1m"] <= low + tolerance).sum())
        if (
            upper_touches < config.minimum_touches_each_side
            or lower_touches < config.minimum_touches_each_side
        ):
            continue
        weights = formation["aggressive_total_quote_1m"].clip(lower=1e-12)
        center = float((formation["close"] * weights).sum() / weights.sum())
        if not low < center < high:
            center = (high + low) / 2.0
        if float(row["vpin_50"]) > float(row["vpin_threshold"]):
            continue
        if abs(float(row["signed_flow_ratio_1m"])) < max(
            config.minimum_directional_flow,
            float(row["flow_abs_threshold"]),
        ):
            continue
        if float(row["impact_efficiency_1m"]) > float(
            row["impact_efficiency_low_threshold"]
        ):
            continue

        current_high = float(row["high_1m"])
        current_low = float(row["low_1m"])
        current_close = float(row["close"])
        upper_sweep = (
            current_high >= high + config.sweep_buffer_atr * atr_value
            and current_close <= high - config.reclaim_depth_atr * atr_value
            and float(row["signed_flow_ratio_1m"]) > 0
            and float(row["ask_depth_change_1m"])
            >= config.minimum_replenishment_fraction
        )
        lower_sweep = (
            current_low <= low - config.sweep_buffer_atr * atr_value
            and current_close >= low + config.reclaim_depth_atr * atr_value
            and float(row["signed_flow_ratio_1m"]) < 0
            and float(row["bid_depth_change_1m"])
            >= config.minimum_replenishment_fraction
        )
        if upper_sweep == lower_sweep:
            continue
        if upper_sweep:
            side = "SELL"
            entry = high
            stop = current_high + config.stop_buffer_atr * atr_value
            target = center
            sweep_direction = 1
            replenishment = float(row["ask_depth_change_1m"])
        else:
            side = "BUY"
            entry = low
            stop = current_low - config.stop_buffer_atr * atr_value
            target = center
            sweep_direction = -1
            replenishment = float(row["bid_depth_change_1m"])
        passive = entry > current_close if side == "SELL" else entry < current_close
        geometry_valid = stop > entry > target if side == "SELL" else stop < entry < target
        if not passive or not geometry_valid:
            continue
        reward_risk = cost_after_reward_risk(
            entry=entry,
            stop=stop,
            target=target,
            side=side,
            costs=passive_costs,
        )
        if not math.isfinite(reward_risk) or not (
            config.minimum_cost_after_rr <= reward_risk <= config.maximum_cost_after_rr
        ):
            continue
        observed_ns = int(observed_time.value)
        signals.append(
            RotationSignal(
                scenario_id=f"v71-{config.auction_minutes}m-{observed_ns}",
                observed_time_ns=observed_ns,
                side=side,
                entry_reference=entry,
                stop_price=stop,
                target_price=target,
                cost_after_reward_risk=reward_risk,
                score=abs(float(row["signed_flow_ratio_1m"]))
                * (1.0 + replenishment),
                max_hold_minutes=config.maximum_holding_minutes,
                source_feature_open_time_ns=observed_ns - NS_MINUTE,
                source_feature_available_time_ns=observed_ns,
                source_max_market_time_ns=observed_ns,
                details={
                    "mode": MODE,
                    "auction_minutes": config.auction_minutes,
                    "auction_high": high,
                    "auction_low": low,
                    "auction_center": center,
                    "auction_width": width,
                    "auction_efficiency": efficiency,
                    "upper_touches": upper_touches,
                    "lower_touches": lower_touches,
                    "sweep_direction": sweep_direction,
                    "sweep_extreme": current_high if upper_sweep else current_low,
                    "signed_flow_ratio_1m": float(row["signed_flow_ratio_1m"]),
                    "same_side_replenishment": replenishment,
                    "impact_efficiency_1m": float(row["impact_efficiency_1m"]),
                    "vpin": float(row["vpin_50"]),
                    "entry_expiry_minutes": config.entry_expiry_minutes,
                    "entry_order_type": "POST_ONLY_LIMIT",
                },
            )
        )
        cooldown_until_ns = (
            observed_ns
            + (config.entry_expiry_minutes + config.cooldown_minutes) * NS_MINUTE
        )

    result: list[RotationSignal] = []
    seen: set[int] = set()
    for signal in sorted(signals, key=lambda item: item.observed_time_ns):
        if signal.observed_time_ns in seen:
            continue
        seen.add(signal.observed_time_ns)
        result.append(signal)
    for signal in result:
        if signal.source_max_market_time_ns > signal.observed_time_ns:
            raise AssertionError("future information detected")
        if signal.source_feature_open_time_ns + NS_MINUTE != signal.observed_time_ns:
            raise AssertionError("sweep close availability mismatch")
    return result
