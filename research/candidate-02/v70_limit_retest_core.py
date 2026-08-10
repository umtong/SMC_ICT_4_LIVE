"""Passive imbalance-retest entries for candidate-02 v70.

A completed three-minute aggressive-flow shock must deplete liquidity in front
of it. One completed minute then confirms that the book remains depleted and
price retains most of the displacement. Instead of buying/selling at that late
confirmation price, the strategy rests a post-only limit at a prelocked fraction
of the shock displacement for five minutes. The limit represents a mechanical
SMC/ICT imbalance/FVG retracement; no fill is assumed outside NautilusTrader.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
import math
from typing import Any, Mapping

import numpy as np
import pandas as pd

import v69_resiliency_core as parent
from v53_nt_core import CostConfig, RotationSignal, cost_after_reward_risk

MODE = "DEPTH_DEPLETION_IMBALANCE_RETEST"
parent.VALID_MODES.add(MODE)
NS_MINUTE = 60_000_000_000


@dataclass(frozen=True, slots=True)
class LimitRetestConfig(parent.ResiliencyConfig):
    limit_entry_fraction: float = 0.50
    limit_entry_expiry_minutes: int = 5
    hold_confirmation_minutes: int = 2
    hold_minimum_retention: float = 0.70
    hold_maximum_recovery_fraction: float = 0.50
    invalidation_retention: float = 0.20
    limit_stop_buffer_atr: float = 0.05
    limit_target_extension: float = 0.75

    @classmethod
    def from_mapping(cls, values: Mapping[str, Any]) -> "LimitRetestConfig":
        unknown = sorted(set(values) - set(cls.__dataclass_fields__))
        if unknown:
            raise ValueError(f"unknown v70 config keys: {unknown}")
        return cls(**dict(values))

    def __post_init__(self) -> None:
        parent.ResiliencyConfig.__post_init__(self)
        if self.mode != MODE:
            raise ValueError("v70 mode changed")
        if self.limit_entry_fraction not in {0.40, 0.50, 0.60}:
            raise ValueError("v70 entry fraction must be 0.40, 0.50 or 0.60")
        if not 1 <= self.limit_entry_expiry_minutes <= 15:
            raise ValueError("invalid limit expiry")
        if not 1 <= self.hold_confirmation_minutes <= 5:
            raise ValueError("invalid hold confirmation window")
        if not self.limit_entry_fraction > self.invalidation_retention >= 0:
            raise ValueError("limit entry must remain beyond invalidation")
        if not self.hold_minimum_retention > self.limit_entry_fraction:
            raise ValueError("confirmation price must remain beyond passive entry")
        if self.limit_stop_buffer_atr < 0 or self.limit_target_extension <= 0:
            raise ValueError("invalid limit stop or target")


def build_state(features: pd.DataFrame, config: LimitRetestConfig) -> pd.DataFrame:
    return parent.build_state(features, config)


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
    config: LimitRetestConfig,
    costs: CostConfig,
) -> list[RotationSignal]:
    start = pd.Timestamp(evaluation_start)
    end = pd.Timestamp(evaluation_end)
    if start.tz is None:
        start = start.tz_localize("UTC")
    if end.tz is None:
        end = end.tz_localize("UTC")
    x = state.join(
        raw[["high", "low"]].rename(columns={"high": "high_1m", "low": "low_1m"}),
        how="inner",
    )
    atr_series = parent._atr(raw, config.atr_lookback_minutes)
    passive_costs = _passive_costs(costs)
    signals: list[RotationSignal] = []
    cooldown_until_ns = -1
    n = config.shock_window_minutes

    for setup_time, row in x.loc[(x.index >= start) & (x.index < end)].iterrows():
        if int(setup_time.value) <= cooldown_until_ns:
            continue
        required = (
            "shock_flow",
            "shock_flow_threshold",
            "shock_turnover",
            "shock_turnover_threshold",
            "shock_return",
            "shock_return_threshold",
            "depletion_threshold",
        )
        if any(not math.isfinite(float(row[name])) for name in required):
            continue
        direction = int(np.sign(float(row["shock_flow"])))
        if direction == 0 or direction * float(row["shock_return"]) <= 0:
            continue
        if abs(float(row["shock_flow"])) < float(row["shock_flow_threshold"]):
            continue
        if float(row["shock_turnover"]) < float(row["shock_turnover_threshold"]):
            continue
        if abs(float(row["shock_return"])) < float(row["shock_return_threshold"]):
            continue

        front_depth_column = "ask_depth_1pct_end" if direction > 0 else "bid_depth_1pct_end"
        depletion_column = "ask_depletion" if direction > 0 else "bid_depletion"
        front_depletion = float(row[depletion_column])
        if front_depletion > -float(row["depletion_threshold"]):
            continue
        pre_time = setup_time - pd.Timedelta(minutes=n)
        if pre_time not in x.index:
            continue
        pre_price = float(x.at[pre_time, "close"])
        setup_price = float(row["close"])
        impulse = abs(setup_price - pre_price)
        pre_depth = float(x.at[pre_time, front_depth_column])
        setup_depth = float(row[front_depth_column])
        depleted_amount = pre_depth - setup_depth
        atr_value = float(atr_series.asof(setup_time))
        if (
            not math.isfinite(impulse)
            or impulse <= 0
            or not math.isfinite(pre_depth)
            or pre_depth <= 0
            or not math.isfinite(depleted_amount)
            or depleted_amount <= 0
            or not math.isfinite(atr_value)
            or atr_value <= 0
        ):
            continue

        future_end = min(
            end,
            setup_time + pd.Timedelta(minutes=config.hold_confirmation_minutes),
        )
        future = x.loc[(x.index > setup_time) & (x.index <= future_end)]
        confirmation: tuple[pd.Timestamp, float, float, float] | None = None
        for observed_time, observed_row in future.iterrows():
            observed_price = float(observed_row["close"])
            observed_depth = float(observed_row[front_depth_column])
            recovery_fraction = (observed_depth - setup_depth) / depleted_amount
            retention = direction * (observed_price - pre_price) / impulse
            directional_flow = direction * float(observed_row["signed_flow_ratio_1m"])
            valid = (
                recovery_fraction <= config.hold_maximum_recovery_fraction
                and retention >= config.hold_minimum_retention
                and directional_flow >= config.continuation_minimum_directional_flow
            )
            if valid:
                confirmation = (
                    pd.Timestamp(observed_time),
                    observed_price,
                    recovery_fraction,
                    retention,
                )
                break
        if confirmation is None:
            continue
        observed_time, confirmation_price, recovery_fraction, retention = confirmation
        limit_price = pre_price + direction * config.limit_entry_fraction * impulse
        invalidation = pre_price + direction * config.invalidation_retention * impulse
        if direction > 0:
            side = "BUY"
            stop = invalidation - config.limit_stop_buffer_atr * atr_value
            target = setup_price + config.limit_target_extension * impulse
            passive = limit_price < confirmation_price
        else:
            side = "SELL"
            stop = invalidation + config.limit_stop_buffer_atr * atr_value
            target = setup_price - config.limit_target_extension * impulse
            passive = limit_price > confirmation_price
        if not passive:
            continue
        geometry_valid = stop < limit_price < target if side == "BUY" else target < limit_price < stop
        if not geometry_valid:
            continue
        reward_risk = cost_after_reward_risk(
            entry=limit_price,
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
                scenario_id=f"v70-limit-{int(config.limit_entry_fraction * 100)}-{observed_ns}",
                observed_time_ns=observed_ns,
                side=side,
                entry_reference=limit_price,
                stop_price=stop,
                target_price=target,
                cost_after_reward_risk=reward_risk,
                score=abs(float(row["shock_flow"])) * (1.0 + abs(front_depletion)),
                max_hold_minutes=config.maximum_holding_minutes,
                source_feature_open_time_ns=observed_ns - NS_MINUTE,
                source_feature_available_time_ns=observed_ns,
                source_max_market_time_ns=observed_ns,
                details={
                    "mode": MODE,
                    "setup_available_utc": pd.Timestamp(setup_time).isoformat(),
                    "pre_shock_time_utc": pd.Timestamp(pre_time).isoformat(),
                    "pre_shock_price": pre_price,
                    "setup_price": setup_price,
                    "confirmation_market_price": confirmation_price,
                    "shock_impulse": impulse,
                    "shock_flow": float(row["shock_flow"]),
                    "shock_turnover": float(row["shock_turnover"]),
                    "front_depth_before": pre_depth,
                    "front_depth_after_shock": setup_depth,
                    "front_depletion_fraction": front_depletion,
                    "confirmation_recovery_fraction": recovery_fraction,
                    "confirmation_price_retention": retention,
                    "confirmation_lag_minutes": int(
                        (observed_time - setup_time) / pd.Timedelta(minutes=1)
                    ),
                    "limit_entry_fraction": config.limit_entry_fraction,
                    "entry_expiry_minutes": config.limit_entry_expiry_minutes,
                    "entry_order_type": "POST_ONLY_LIMIT",
                },
            )
        )
        cooldown_until_ns = (
            observed_ns
            + (config.limit_entry_expiry_minutes + config.cooldown_minutes) * NS_MINUTE
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
            raise AssertionError("limit release availability mismatch")
    return result
