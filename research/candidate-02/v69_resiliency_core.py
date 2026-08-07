"""Post-shock liquidity resiliency state machine for candidate-02 v69.

A completed three-minute aggressive-flow shock first removes liquidity in front
of the flow. The next completed minutes classify the book's resiliency:

* SLOW_RECOVERY_CONTINUATION: front-side depth remains depleted, the price keeps
  most of the shock displacement, and new flow does not materially oppose it.
  The depleted book has not rebuilt, so price discovery can continue.
* FAST_RECOVERY_REVERSAL: front-side depth rapidly rebuilds, price loses at
  least half the shock displacement, and flow turns against the shock. The book
  absorbed the order and the price rotates toward the pre-shock origin.

Only causal trade intents are emitted. NautilusTrader is the sole performance,
order, fill, fee, position, account and NAV engine.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any, Mapping

import numpy as np
import pandas as pd

from v53_nt_core import CostConfig, RotationSignal
from v61_family_core import cost_after_reward_risk

NS_MINUTE = 60_000_000_000
VALID_MODES = {"SLOW_RECOVERY_CONTINUATION", "FAST_RECOVERY_REVERSAL"}


@dataclass(frozen=True, slots=True)
class ResiliencyConfig:
    mode: str
    shock_window_minutes: int = 3
    prior_window_minutes: int = 720
    prior_minimum_minutes: int = 180
    flow_abs_quantile: float = 0.65
    volume_quantile: float = 0.60
    return_abs_quantile: float = 0.60
    depletion_abs_quantile: float = 0.60
    recovery_window_minutes: int = 3
    slow_maximum_recovery_fraction: float = 0.50
    continuation_minimum_retention: float = 0.55
    continuation_minimum_directional_flow: float = -0.05
    fast_minimum_recovery_fraction: float = 0.80
    reversal_maximum_retention: float = 0.50
    reversal_maximum_directional_flow: float = 0.0
    continuation_invalidation_retention: float = 0.35
    stop_buffer_atr: float = 0.10
    continuation_target_extension: float = 0.75
    reversal_target_overshoot: float = 0.0
    atr_lookback_minutes: int = 60
    cooldown_minutes: int = 10
    maximum_holding_minutes: int = 90
    minimum_cost_after_rr: float = 0.70
    maximum_cost_after_rr: float = 5.0
    flow_floor: float = 0.05

    @classmethod
    def from_mapping(cls, values: Mapping[str, Any]) -> "ResiliencyConfig":
        unknown = sorted(set(values) - set(cls.__dataclass_fields__))
        if unknown:
            raise ValueError(f"unknown v69 config keys: {unknown}")
        return cls(**dict(values))

    def __post_init__(self) -> None:
        if self.mode not in VALID_MODES:
            raise ValueError(f"unknown v69 mode: {self.mode}")
        if not 2 <= self.shock_window_minutes <= 10:
            raise ValueError("shock window must be 2-10 minutes")
        if self.prior_minimum_minutes >= self.prior_window_minutes:
            raise ValueError("prior minimum must be below prior window")
        for name in (
            "flow_abs_quantile",
            "volume_quantile",
            "return_abs_quantile",
            "depletion_abs_quantile",
        ):
            if not 0 < float(getattr(self, name)) < 1:
                raise ValueError(f"{name} must be in (0, 1)")
        if self.recovery_window_minutes not in {2, 3, 5}:
            raise ValueError("v69 recovery window must be one of 2, 3 or 5 minutes")
        if not 0 <= self.slow_maximum_recovery_fraction <= 1:
            raise ValueError("invalid slow recovery fraction")
        if not 0 <= self.fast_minimum_recovery_fraction <= 2:
            raise ValueError("invalid fast recovery fraction")
        if not 0 < self.continuation_minimum_retention <= 1:
            raise ValueError("invalid continuation retention")
        if not 0 <= self.reversal_maximum_retention < 1:
            raise ValueError("invalid reversal retention")
        if not 0 < self.continuation_invalidation_retention < self.continuation_minimum_retention:
            raise ValueError("invalid continuation invalidation")
        if self.continuation_target_extension <= 0 or self.maximum_holding_minutes <= 0:
            raise ValueError("invalid target or holding period")
        if not 0 < self.minimum_cost_after_rr <= self.maximum_cost_after_rr:
            raise ValueError("invalid cost-after RR band")


def _prior_quantile(series: pd.Series, config: ResiliencyConfig, quantile: float) -> pd.Series:
    return series.shift(1).rolling(
        config.prior_window_minutes,
        min_periods=config.prior_minimum_minutes,
    ).quantile(quantile)


def build_state(features: pd.DataFrame, config: ResiliencyConfig) -> pd.DataFrame:
    x = features.copy()
    n = config.shock_window_minutes
    signed = x["aggressive_signed_quote_1m"].rolling(n, min_periods=n).sum()
    total = x["aggressive_total_quote_1m"].rolling(n, min_periods=n).sum()
    x["shock_flow"] = signed / total.replace(0.0, np.nan)
    x["shock_turnover"] = total
    x["shock_return"] = np.log(x["close"] / x["close"].shift(n))
    x["ask_depletion"] = x["ask_depth_1pct_end"] / x["ask_depth_1pct_end"].shift(n) - 1.0
    x["bid_depletion"] = x["bid_depth_1pct_end"] / x["bid_depth_1pct_end"].shift(n) - 1.0
    x["shock_flow_threshold"] = _prior_quantile(x["shock_flow"].abs(), config, config.flow_abs_quantile)
    x["shock_turnover_threshold"] = _prior_quantile(x["shock_turnover"], config, config.volume_quantile)
    x["shock_return_threshold"] = _prior_quantile(x["shock_return"].abs(), config, config.return_abs_quantile)
    depth_abs = pd.concat([x["ask_depletion"].abs(), x["bid_depletion"].abs()], axis=1).max(axis=1)
    x["depletion_threshold"] = _prior_quantile(depth_abs, config, config.depletion_abs_quantile)
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


def _append(
    signals: list[RotationSignal],
    *,
    config: ResiliencyConfig,
    costs: CostConfig,
    observed: pd.Timestamp,
    side: str,
    entry: float,
    stop: float,
    target: float,
    score: float,
    details: Mapping[str, Any],
) -> bool:
    geometry_valid = stop < entry < target if side == "BUY" else target < entry < stop
    if not geometry_valid:
        return False
    reward_risk = cost_after_reward_risk(
        entry=entry,
        stop=stop,
        target=target,
        side=side,
        costs=costs,
    )
    if not math.isfinite(reward_risk) or not (
        config.minimum_cost_after_rr <= reward_risk <= config.maximum_cost_after_rr
    ):
        return False
    observed_ns = int(observed.value)
    signals.append(
        RotationSignal(
            scenario_id=f"v69-{config.mode.lower()}-{observed_ns}",
            observed_time_ns=observed_ns,
            side=side,
            entry_reference=entry,
            stop_price=stop,
            target_price=target,
            cost_after_reward_risk=reward_risk,
            score=float(score),
            max_hold_minutes=config.maximum_holding_minutes,
            source_feature_open_time_ns=observed_ns - NS_MINUTE,
            source_feature_available_time_ns=observed_ns,
            source_max_market_time_ns=observed_ns,
            details={"mode": config.mode, **dict(details)},
        )
    )
    return True


def build_rotation_signals(
    *,
    state: pd.DataFrame,
    raw: pd.DataFrame,
    evaluation_start: pd.Timestamp,
    evaluation_end: pd.Timestamp,
    config: ResiliencyConfig,
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
    atr_series = _atr(raw, config.atr_lookback_minutes)
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
        shock_raw = raw.loc[(raw.index > pre_time) & (raw.index <= setup_time)]
        if shock_raw.empty:
            continue
        shock_extreme = float(shock_raw["high"].max() if direction > 0 else shock_raw["low"].min())
        future_end = min(
            end,
            setup_time + pd.Timedelta(minutes=config.recovery_window_minutes),
        )
        future = x.loc[(x.index > setup_time) & (x.index <= future_end)]
        if future.empty:
            continue

        confirmation: tuple[pd.Timestamp, float, float, float] | None = None
        for observed_time, observed_row in future.iterrows():
            observed_price = float(observed_row["close"])
            observed_depth = float(observed_row[front_depth_column])
            recovery_fraction = (observed_depth - setup_depth) / depleted_amount
            retention = direction * (observed_price - pre_price) / impulse
            directional_flow = direction * float(observed_row["signed_flow_ratio_1m"])
            if config.mode == "SLOW_RECOVERY_CONTINUATION":
                valid = (
                    recovery_fraction <= config.slow_maximum_recovery_fraction
                    and retention >= config.continuation_minimum_retention
                    and directional_flow >= config.continuation_minimum_directional_flow
                )
            else:
                valid = (
                    recovery_fraction >= config.fast_minimum_recovery_fraction
                    and retention <= config.reversal_maximum_retention
                    and directional_flow <= config.reversal_maximum_directional_flow
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
        observed_time, entry, recovery_fraction, retention = confirmation

        if config.mode == "SLOW_RECOVERY_CONTINUATION":
            invalidation = pre_price + direction * config.continuation_invalidation_retention * impulse
            if direction > 0:
                side = "BUY"
                stop = invalidation - config.stop_buffer_atr * atr_value
                target = setup_price + config.continuation_target_extension * impulse
            else:
                side = "SELL"
                stop = invalidation + config.stop_buffer_atr * atr_value
                target = setup_price - config.continuation_target_extension * impulse
        else:
            if direction > 0:
                side = "SELL"
                stop = shock_extreme + config.stop_buffer_atr * atr_value
                target = pre_price - config.reversal_target_overshoot * impulse
            else:
                side = "BUY"
                stop = shock_extreme - config.stop_buffer_atr * atr_value
                target = pre_price + config.reversal_target_overshoot * impulse

        emitted = _append(
            signals,
            config=config,
            costs=costs,
            observed=observed_time,
            side=side,
            entry=entry,
            stop=stop,
            target=target,
            score=abs(float(row["shock_flow"])) * (1.0 + abs(front_depletion)),
            details={
                "setup_available_utc": pd.Timestamp(setup_time).isoformat(),
                "pre_shock_time_utc": pd.Timestamp(pre_time).isoformat(),
                "pre_shock_price": pre_price,
                "setup_price": setup_price,
                "shock_extreme": shock_extreme,
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
                "recovery_window_minutes": config.recovery_window_minutes,
            },
        )
        if emitted:
            cooldown_until_ns = int(observed_time.value) + config.cooldown_minutes * NS_MINUTE

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
            raise AssertionError("confirmation availability mismatch")
    return result
