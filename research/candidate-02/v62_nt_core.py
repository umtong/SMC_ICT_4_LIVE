"""Variable-duration trend pullback state for candidate-02 v62."""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any, Mapping

import numpy as np
import pandas as pd

import v61_family_core as family
from v53_nt_core import CostConfig, RotationSignal

NS_MINUTE = 60_000_000_000


@dataclass(frozen=True, slots=True)
class VariablePullbackConfig(family.ScenarioFamilyConfig):
    minimum_pullback_bars_5m: int = 2
    maximum_pullback_bars_5m: int = 6
    pullback_selection: str = "DEEPEST_VALID"
    require_previous_bar_countertrend_or_stalled: bool = True

    @classmethod
    def from_mapping(cls, values: Mapping[str, Any]) -> "VariablePullbackConfig":
        allowed = set(cls.__dataclass_fields__)
        unknown = sorted(set(values) - allowed)
        if unknown:
            raise ValueError(f"unknown v62 config keys: {unknown}")
        return cls(**dict(values))

    def __post_init__(self) -> None:
        family.ScenarioFamilyConfig.__post_init__(self)
        if self.mode != "TREND_PULLBACK_REACCELERATION":
            raise ValueError("v62 supports only trend pullback reacceleration")
        if not 2 <= self.minimum_pullback_bars_5m <= self.maximum_pullback_bars_5m:
            raise ValueError("invalid variable pullback bar range")
        if self.maximum_pullback_bars_5m > 12:
            raise ValueError("pullback cannot exceed the one-hour trend horizon")
        if self.pullback_selection != "DEEPEST_VALID":
            raise ValueError("v62 pullback selection must remain DEEPEST_VALID")


def build_state(features: pd.DataFrame, config: VariablePullbackConfig) -> pd.DataFrame:
    return family.build_state(features, config)


def build_rotation_signals(
    *,
    state: pd.DataFrame,
    raw: pd.DataFrame,
    evaluation_start: pd.Timestamp,
    evaluation_end: pd.Timestamp,
    config: VariablePullbackConfig,
    costs: CostConfig,
) -> list[RotationSignal]:
    start = family._utc(evaluation_start)
    end = family._utc(evaluation_end)
    five = family._aggregate_raw_five_minute(raw)
    aligned = state.join(
        five[["open", "high", "low", "close", "minute_count"]].rename(
            columns={
                "open": "raw_open_5m",
                "high": "raw_high_5m",
                "low": "raw_low_5m",
                "close": "raw_close_5m",
            }
        ),
        how="inner",
    )
    if aligned.empty:
        raise ValueError("no aligned feature and raw five-minute bars")
    atr = family._true_range(raw).rolling(
        config.atr_lookback_minutes,
        min_periods=max(30, config.atr_lookback_minutes // 2),
    ).median()

    signals: list[RotationSignal] = []
    cooldown_until_ns = -1
    minimum_history = max(16, config.maximum_pullback_bars_5m + 12)
    for i in range(minimum_history, len(aligned)):
        feature_open = aligned.index[i]
        observed_time = feature_open + pd.Timedelta(minutes=5)
        if not start <= observed_time < end:
            continue
        observed_ns = int(observed_time.value)
        if observed_ns <= cooldown_until_ns or observed_time not in raw.index:
            continue

        row = aligned.iloc[i]
        prior = aligned.iloc[i - 1]
        trend_return = float(prior["log_ret_60m"])
        direction = int(np.sign(trend_return))
        if (
            direction == 0
            or abs(trend_return) < float(prior["trend_threshold"])
            or direction * float(row["log_ret_5m"]) <= 0
            or direction * float(row["signed_flow"]) < config.minimum_confirmation_flow
            or direction * float(row["depth_imbalance_1pct"]) < config.minimum_confirmation_depth
            or float(row["oi_change_1h"]) <= 0
            or float(row["vpin_50"]) > float(row["vpin_high_threshold"])
        ):
            continue
        if (
            config.require_previous_bar_countertrend_or_stalled
            and direction * float(prior["log_ret_5m"]) > 0
        ):
            continue

        valid: list[dict[str, float | int]] = []
        for bars in range(
            config.minimum_pullback_bars_5m,
            config.maximum_pullback_bars_5m + 1,
        ):
            start_row = aligned.iloc[i - (bars + 1)]
            pullback_start = float(start_row["raw_close_5m"])
            pullback_end = float(prior["raw_close_5m"])
            if pullback_start <= 0 or pullback_end <= 0:
                continue
            pullback_return = math.log(pullback_end / pullback_start)
            pullback_fraction = abs(pullback_return) / max(abs(trend_return), 1e-12)
            if (
                direction * pullback_return < 0
                and config.pullback_minimum_fraction
                <= pullback_fraction
                <= config.pullback_maximum_fraction
            ):
                valid.append(
                    {
                        "bars": bars,
                        "start": pullback_start,
                        "end": pullback_end,
                        "return": pullback_return,
                        "fraction": pullback_fraction,
                    }
                )
        if not valid:
            continue
        selected = max(valid, key=lambda value: (abs(float(value["return"])), int(value["bars"])))
        bars = int(selected["bars"])
        pullback_start = float(selected["start"])
        pullback_end = float(selected["end"])
        pullback_return = float(selected["return"])
        pullback_fraction = float(selected["fraction"])

        atr_value = float(atr.asof(observed_time))
        if not math.isfinite(atr_value) or atr_value <= 0:
            continue
        entry = float(raw.at[observed_time, "close"])
        pullback_window = aligned.iloc[i - bars : i + 1]
        prior_trend_window = aligned.iloc[i - 12 : i]
        pullback_distance = abs(pullback_end - pullback_start)
        if direction > 0:
            side = "BUY"
            stop = float(
                pullback_window["raw_low_5m"].min()
                - config.stop_buffer_atr * atr_value
            )
            external = float(prior_trend_window["raw_high_5m"].max())
            target = external + config.target_pullback_extension * pullback_distance
        else:
            side = "SELL"
            stop = float(
                pullback_window["raw_high_5m"].max()
                + config.stop_buffer_atr * atr_value
            )
            external = float(prior_trend_window["raw_low_5m"].min())
            target = external - config.target_pullback_extension * pullback_distance

        emitted = family._emit(
            signals=signals,
            mode="VARIABLE_DURATION_TREND_PULLBACK",
            observed_time=observed_time,
            feature_open=feature_open,
            side=side,
            entry=entry,
            stop=stop,
            target=target,
            max_hold=config.maximum_holding_minutes,
            score=abs(trend_return) * (1.0 + abs(float(row["signed_flow"]))),
            details={
                "trend_return_60m": trend_return,
                "trend_threshold": float(prior["trend_threshold"]),
                "selected_pullback_bars_5m": bars,
                "selected_pullback_minutes": bars * 5,
                "valid_pullback_horizons_5m": [int(value["bars"]) for value in valid],
                "pullback_return": pullback_return,
                "pullback_fraction": pullback_fraction,
                "prior_external_liquidity": external,
                "previous_bar_trend_aligned_return": direction * float(prior["log_ret_5m"]),
                "confirmation_flow": direction * float(row["signed_flow"]),
                "confirmation_depth": direction * float(row["depth_imbalance_1pct"]),
                "oi_change_1h": float(row["oi_change_1h"]),
                "vpin": float(row["vpin_50"]),
                "atr_1m": atr_value,
            },
            costs=costs,
            config=config,
        )
        if emitted:
            cooldown_until_ns = observed_ns + config.cooldown_bars * 5 * NS_MINUTE

    signals.sort(key=lambda item: item.observed_time_ns)
    if len({item.observed_time_ns for item in signals}) != len(signals):
        raise ValueError("duplicate v62 signal timestamps")
    for item in signals:
        if item.source_max_market_time_ns > item.observed_time_ns:
            raise AssertionError("future information detected")
        if item.source_feature_open_time_ns + 5 * NS_MINUTE != item.observed_time_ns:
            raise AssertionError("feature availability mismatch")
    return signals
