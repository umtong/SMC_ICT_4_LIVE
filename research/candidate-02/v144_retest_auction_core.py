"""Causal competing-auction breakout/retest resolver for candidate-02 v144.

A stretched auction is detected by v107.  The first completed one-minute close
outside the five-minute confirmation range selects rotation or continuation.
The order is not allowed to chase that break: the broken boundary must be
retested and hold on a later completed bar, and activation occurs exactly one
completed minute after that retest.  NautilusTrader remains the only execution
and accounting engine.
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
class RetestAuctionConfig(RegimeRotationConfig):
    decision_window_minutes: int = 15
    retest_window_minutes: int = 10
    activation_delay_minutes: int = 1
    retest_tolerance_atr: float = 0.10
    retest_stop_buffer_atr: float = 0.10
    continuation_extension: float = 1.0

    @classmethod
    def from_mapping(cls, values: Mapping[str, Any]) -> "RetestAuctionConfig":
        data = dict(values)
        data["auction_windows_5m"] = tuple(int(value) for value in data["auction_windows_5m"])
        allowed = set(cls.__dataclass_fields__)
        unknown = sorted(set(data) - allowed)
        if unknown:
            raise ValueError(f"unknown v144 config keys: {unknown}")
        return cls(**data)

    def __post_init__(self) -> None:
        RegimeRotationConfig.__post_init__(self)
        if self.decision_window_minutes <= 0 or self.retest_window_minutes <= 0:
            raise ValueError("v144 decision and retest windows must be positive")
        if self.activation_delay_minutes != 1:
            raise ValueError("v144 activation delay must be exactly one minute")
        if self.retest_tolerance_atr < 0 or self.retest_stop_buffer_atr < 0:
            raise ValueError("v144 retest tolerances cannot be negative")
        if self.continuation_extension <= 0:
            raise ValueError("v144 continuation extension must be positive")


def build_state(features: pd.DataFrame, config: RetestAuctionConfig) -> pd.DataFrame:
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
    config: RetestAuctionConfig,
    costs: CostConfig,
) -> list[RotationSignal]:
    start = _normalize(evaluation_start)
    end = _normalize(evaluation_end)
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
        high = float(confirmation["high"].max())
        low = float(confirmation["low"].min())
        width = high - low
        atr = float(event.details["atr_1m"])
        if not all(math.isfinite(value) for value in (high, low, width, atr)) or width <= 0 or atr <= 0:
            continue

        resolution_time: pd.Timestamp | None = None
        resolution_close = math.nan
        route: str | None = None
        side: str | None = None
        decision = raw.loc[
            (raw.index > event_time)
            & (raw.index <= event_time + pd.Timedelta(minutes=config.decision_window_minutes))
        ]
        for timestamp, bar in decision.iterrows():
            close = float(bar["close"])
            if event.side == "SELL":
                if close < low:
                    route, side = "ROTATION", "SELL"
                elif close > high:
                    route, side = "CONTINUATION", "BUY"
            else:
                if close > high:
                    route, side = "ROTATION", "BUY"
                elif close < low:
                    route, side = "CONTINUATION", "SELL"
            if route is not None:
                resolution_time = timestamp
                resolution_close = close
                break
        if resolution_time is None or route is None or side is None:
            continue

        broken_level = high if side == "BUY" else low
        retest_time: pd.Timestamp | None = None
        retest_low = math.nan
        retest_high = math.nan
        retest = raw.loc[
            (raw.index > resolution_time)
            & (raw.index <= resolution_time + pd.Timedelta(minutes=config.retest_window_minutes))
        ]
        for timestamp, bar in retest.iterrows():
            bar_low = float(bar["low"])
            bar_high = float(bar["high"])
            close = float(bar["close"])
            if side == "BUY":
                # A close through the opposite boundary invalidates the won auction.
                if close <= low:
                    break
                held = bar_low <= broken_level + config.retest_tolerance_atr * atr and close > broken_level
            else:
                if close >= high:
                    break
                held = bar_high >= broken_level - config.retest_tolerance_atr * atr and close < broken_level
            if held:
                retest_time = timestamp
                retest_low = bar_low
                retest_high = bar_high
                break
        if retest_time is None:
            continue

        activation_time = retest_time + pd.Timedelta(minutes=config.activation_delay_minutes)
        if not start <= activation_time < end or activation_time not in raw.index:
            continue
        entry = float(raw.at[activation_time, "close"])
        if route == "ROTATION":
            target = float(event.details["auction_center"])
        elif side == "BUY":
            target = resolution_close + config.continuation_extension * width
        else:
            target = resolution_close - config.continuation_extension * width

        if side == "BUY":
            stop = min(retest_low, broken_level - config.retest_stop_buffer_atr * atr)
            geometry = stop < entry < target
        else:
            stop = max(retest_high, broken_level + config.retest_stop_buffer_atr * atr)
            geometry = target < entry < stop
        if not geometry:
            continue

        rr = cost_after_reward_risk(
            entry=entry,
            stop=stop,
            target=target,
            side=side,
            costs=costs,
        )
        if not math.isfinite(rr) or not (
            config.minimum_cost_after_rr <= rr <= config.maximum_cost_after_rr
        ):
            continue

        activation_ns = int(activation_time.value)
        retest_ns = int(retest_time.value)
        details = dict(event.details)
        details.update({
            "v144_event_time_ns":event.observed_time_ns,
            "v144_resolution_time_ns":int(resolution_time.value),
            "v144_retest_time_ns":retest_ns,
            "v144_activation_time_ns":activation_ns,
            "competition_result":route,
            "execution_side":side,
            "confirmation_high":high,
            "confirmation_low":low,
            "confirmation_range":width,
            "broken_level":broken_level,
            "resolution_close":resolution_close,
            "retest_low":retest_low,
            "retest_high":retest_high,
            "retest_window_minutes":config.retest_window_minutes,
            "natural_target":"PRE_OBSERVED_AUCTION_CENTER" if route == "ROTATION" else "CONFIRMED_BREAK_MEASURED_MOVE",
        })
        signal = replace(
            event,
            scenario_id=f"v144-retest-{route.lower()}-{activation_ns}",
            observed_time_ns=activation_ns,
            side=side,
            entry_reference=entry,
            stop_price=stop,
            target_price=target,
            cost_after_reward_risk=rr,
            score=rr + float(event.score),
            max_hold_minutes=config.maximum_holding_minutes,
            source_feature_available_time_ns=activation_ns,
            source_max_market_time_ns=retest_ns,
            details=details,
        )
        prior = selected.get(activation_ns)
        if prior is None or signal.score > prior.score:
            selected[activation_ns] = signal

    result = sorted(selected.values(), key=lambda signal: signal.observed_time_ns)
    for signal in result:
        if signal.source_feature_available_time_ns != signal.observed_time_ns:
            raise AssertionError("v144 executable availability mismatch")
        if signal.observed_time_ns - signal.source_max_market_time_ns != NS_MINUTE:
            raise AssertionError("v144 activation is not one completed minute after retest")
    return result
