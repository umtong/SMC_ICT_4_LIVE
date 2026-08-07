"""Causal competing-auction resolver for candidate-02 v110.

The v107 high-dispersion/low-efficiency auction is only an event detector.  A
trade is emitted only when, after the original reversal confirmation, completed
one-minute closes resolve the competition in the rotation direction before
price re-accepts the excursion direction.  NautilusTrader owns all execution,
fees, positions and NAV.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
import math
from typing import Any, Mapping

import pandas as pd

from v53_nt_core import CostConfig, RotationSignal, cost_after_reward_risk
from v107_regime_rotation_core import (
    RegimeRotationConfig,
    build_rotation_signals as _build_v107_signals,
    build_state as _build_v107_state,
)

UTC = "UTC"
NS_MINUTE = 60_000_000_000


@dataclass(frozen=True, slots=True)
class CompetingAuctionConfig(RegimeRotationConfig):
    decision_window_minutes: int = 15
    activation_delay_minutes: int = 1
    resolution_stop_buffer_atr: float = 0.15

    @classmethod
    def from_mapping(cls, values: Mapping[str, Any]) -> "CompetingAuctionConfig":
        data = dict(values)
        data["auction_windows_5m"] = tuple(int(value) for value in data["auction_windows_5m"])
        allowed = set(cls.__dataclass_fields__)
        unknown = sorted(set(data) - allowed)
        if unknown:
            raise ValueError(f"unknown v110 config keys: {unknown}")
        return cls(**data)

    def __post_init__(self) -> None:
        RegimeRotationConfig.__post_init__(self)
        if self.decision_window_minutes <= 0:
            raise ValueError("v110 decision window must be positive")
        if self.activation_delay_minutes != 1:
            raise ValueError("v110 keeps exactly one completed minute between resolution and order activation")
        if self.resolution_stop_buffer_atr < 0:
            raise ValueError("v110 stop buffer cannot be negative")


def build_state(features: pd.DataFrame, config: CompetingAuctionConfig) -> pd.DataFrame:
    return _build_v107_state(features, config)


def _normalize(value: pd.Timestamp) -> pd.Timestamp:
    value = pd.Timestamp(value)
    return value.tz_localize(UTC) if value.tzinfo is None else value.tz_convert(UTC)


def build_rotation_signals(
    *,
    state: pd.DataFrame,
    raw: pd.DataFrame,
    evaluation_start: pd.Timestamp,
    evaluation_end: pd.Timestamp,
    config: CompetingAuctionConfig,
    costs: CostConfig,
) -> list[RotationSignal]:
    start = _normalize(evaluation_start)
    end = _normalize(evaluation_end)

    # Generate causal auction events without letting the old symmetric target
    # decide which events exist.  This is detection, not execution.
    detector = replace(
        config,
        target_extension=0.0,
        minimum_cost_after_rr=0.01,
        maximum_cost_after_rr=100.0,
    )
    events = _build_v107_signals(
        state=state,
        raw=raw,
        evaluation_start=start,
        evaluation_end=end,
        config=detector,
        costs=costs,
    )

    selected: dict[int, RotationSignal] = {}
    for event in events:
        event_time = pd.Timestamp(event.observed_time_ns, unit="ns", tz="UTC")
        feature_time = pd.Timestamp(event.source_feature_open_time_ns, unit="ns", tz="UTC")
        confirmation = raw.loc[(raw.index > feature_time) & (raw.index <= event_time)]
        if len(confirmation) != 5:
            continue
        confirmation_high = float(confirmation["high"].max())
        confirmation_low = float(confirmation["low"].min())

        resolution_time: pd.Timestamp | None = None
        continuation_won = False
        decision_bars = raw.loc[
            (raw.index > event_time)
            & (raw.index <= event_time + pd.Timedelta(minutes=config.decision_window_minutes))
        ]
        for timestamp, bar in decision_bars.iterrows():
            close = float(bar["close"])
            if event.side == "SELL":
                rotation_break = close < confirmation_low
                continuation_break = close > confirmation_high
            else:
                rotation_break = close > confirmation_high
                continuation_break = close < confirmation_low
            if rotation_break:
                resolution_time = timestamp
                break
            if continuation_break:
                continuation_won = True
                break
        if continuation_won or resolution_time is None:
            continue

        activation_time = resolution_time + pd.Timedelta(minutes=config.activation_delay_minutes)
        if not start <= activation_time < end or activation_time not in raw.index:
            continue
        entry = float(raw.at[activation_time, "close"])
        atr = float(event.details["atr_1m"])
        target = float(event.details["auction_center"])
        if event.side == "SELL":
            stop = confirmation_high + config.resolution_stop_buffer_atr * atr
            geometry = target < entry < stop
        else:
            stop = confirmation_low - config.resolution_stop_buffer_atr * atr
            geometry = stop < entry < target
        if not geometry:
            continue

        rr = cost_after_reward_risk(
            entry=entry,
            stop=stop,
            target=target,
            side=event.side,
            costs=costs,
        )
        if not math.isfinite(rr) or not (
            config.minimum_cost_after_rr <= rr <= config.maximum_cost_after_rr
        ):
            continue

        activation_ns = int(activation_time.value)
        resolution_ns = int(resolution_time.value)
        details = dict(event.details)
        details.update(
            {
                "v110_event_time_ns": event.observed_time_ns,
                "v110_resolution_time_ns": resolution_ns,
                "v110_activation_time_ns": activation_ns,
                "decision_window_minutes": config.decision_window_minutes,
                "confirmation_high": confirmation_high,
                "confirmation_low": confirmation_low,
                "competition_result": "ROTATION_BREAK_FIRST",
                "natural_target": "PRE_OBSERVED_AUCTION_CENTER",
            }
        )
        signal = replace(
            event,
            scenario_id=f"v110-competing-rotation-{activation_ns}",
            observed_time_ns=activation_ns,
            entry_reference=entry,
            stop_price=stop,
            target_price=target,
            cost_after_reward_risk=rr,
            score=rr + float(event.score),
            max_hold_minutes=config.maximum_holding_minutes,
            source_feature_available_time_ns=event.observed_time_ns,
            source_max_market_time_ns=resolution_ns,
            details=details,
        )
        prior = selected.get(activation_ns)
        if prior is None or signal.score > prior.score:
            selected[activation_ns] = signal

    result = sorted(selected.values(), key=lambda signal: signal.observed_time_ns)
    for signal in result:
        if signal.source_max_market_time_ns >= signal.observed_time_ns:
            raise AssertionError("v110 requires one completed minute after the resolution decision")
        if signal.observed_time_ns - signal.source_max_market_time_ns != NS_MINUTE:
            raise AssertionError("v110 activation delay is not exactly one minute")
    return result
