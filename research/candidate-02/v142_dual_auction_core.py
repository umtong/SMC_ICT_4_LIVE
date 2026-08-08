"""Causal dual-route competing-auction resolver for candidate-02 v142.

The v107 detector identifies a stretched, high-dispersion/low-efficiency
auction.  No order is submitted at detection.  Completed one-minute closes
must first break one side of the five-minute confirmation range.  A rotation
break routes toward the pre-observed auction center; a continuation break
routes in the excursion direction toward a measured move.  Signal activation
is exactly one completed minute after the deciding close.  NautilusTrader is
the sole execution, fee, position, and NAV engine.
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
class DualAuctionConfig(RegimeRotationConfig):
    decision_window_minutes: int = 15
    activation_delay_minutes: int = 1
    resolution_stop_buffer_atr: float = 0.15
    continuation_extension: float = 1.0

    @classmethod
    def from_mapping(cls, values: Mapping[str, Any]) -> "DualAuctionConfig":
        data = dict(values)
        data["auction_windows_5m"] = tuple(int(value) for value in data["auction_windows_5m"])
        allowed = set(cls.__dataclass_fields__)
        unknown = sorted(set(data) - allowed)
        if unknown:
            raise ValueError(f"unknown v142 config keys: {unknown}")
        return cls(**data)

    def __post_init__(self) -> None:
        RegimeRotationConfig.__post_init__(self)
        if self.decision_window_minutes <= 0:
            raise ValueError("v142 decision window must be positive")
        if self.activation_delay_minutes != 1:
            raise ValueError("v142 activation delay must be exactly one completed minute")
        if self.resolution_stop_buffer_atr < 0:
            raise ValueError("v142 stop buffer cannot be negative")
        if self.continuation_extension <= 0:
            raise ValueError("v142 continuation extension must be positive")


def build_state(features: pd.DataFrame, config: DualAuctionConfig) -> pd.DataFrame:
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
    config: DualAuctionConfig,
    costs: CostConfig,
) -> list[RotationSignal]:
    start = _normalize(evaluation_start)
    end = _normalize(evaluation_end)

    # Event detection must not depend on the old symmetric objective or on the
    # route selected after detection.  A very low RR floor is used only while
    # materializing candidate events; executable geometry is checked below.
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
        confirmation_range = confirmation_high - confirmation_low
        if not math.isfinite(confirmation_range) or confirmation_range <= 0:
            continue

        resolution_time: pd.Timestamp | None = None
        resolution_close = math.nan
        route: str | None = None
        execution_side: str | None = None
        decision_bars = raw.loc[
            (raw.index > event_time)
            & (raw.index <= event_time + pd.Timedelta(minutes=config.decision_window_minutes))
        ]
        for timestamp, bar in decision_bars.iterrows():
            close = float(bar["close"])
            if event.side == "SELL":
                rotation_break = close < confirmation_low
                continuation_break = close > confirmation_high
                if rotation_break:
                    route, execution_side = "ROTATION", "SELL"
                elif continuation_break:
                    route, execution_side = "CONTINUATION", "BUY"
            else:
                rotation_break = close > confirmation_high
                continuation_break = close < confirmation_low
                if rotation_break:
                    route, execution_side = "ROTATION", "BUY"
                elif continuation_break:
                    route, execution_side = "CONTINUATION", "SELL"
            if route is not None:
                resolution_time = timestamp
                resolution_close = close
                break

        if resolution_time is None or execution_side is None or route is None:
            continue
        activation_time = resolution_time + pd.Timedelta(minutes=config.activation_delay_minutes)
        if not start <= activation_time < end or activation_time not in raw.index:
            continue

        entry = float(raw.at[activation_time, "close"])
        atr = float(event.details["atr_1m"])
        if not math.isfinite(entry) or not math.isfinite(atr) or atr <= 0:
            continue

        if route == "ROTATION":
            target = float(event.details["auction_center"])
            if execution_side == "SELL":
                stop = confirmation_high + config.resolution_stop_buffer_atr * atr
                geometry = target < entry < stop
            else:
                stop = confirmation_low - config.resolution_stop_buffer_atr * atr
                geometry = stop < entry < target
        else:
            if execution_side == "BUY":
                stop = confirmation_low - config.resolution_stop_buffer_atr * atr
                target = resolution_close + config.continuation_extension * confirmation_range
                geometry = stop < entry < target
            else:
                stop = confirmation_high + config.resolution_stop_buffer_atr * atr
                target = resolution_close - config.continuation_extension * confirmation_range
                geometry = target < entry < stop
        if not geometry:
            continue

        rr = cost_after_reward_risk(
            entry=entry,
            stop=stop,
            target=target,
            side=execution_side,
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
                "v142_event_time_ns": event.observed_time_ns,
                "v142_resolution_time_ns": resolution_ns,
                "v142_activation_time_ns": activation_ns,
                "decision_window_minutes": config.decision_window_minutes,
                "confirmation_high": confirmation_high,
                "confirmation_low": confirmation_low,
                "confirmation_range": confirmation_range,
                "resolution_close": resolution_close,
                "competition_result": route,
                "continuation_extension": config.continuation_extension,
                "natural_target": (
                    "PRE_OBSERVED_AUCTION_CENTER"
                    if route == "ROTATION"
                    else "CONFIRMED_BREAK_MEASURED_MOVE"
                ),
                "original_rotation_side": event.side,
            }
        )
        route_bonus = 0.25 if route == "ROTATION" else 0.0
        signal = replace(
            event,
            scenario_id=f"v142-dual-{route.lower()}-{activation_ns}",
            observed_time_ns=activation_ns,
            side=execution_side,
            entry_reference=entry,
            stop_price=stop,
            target_price=target,
            cost_after_reward_risk=rr,
            score=rr + float(event.score) + route_bonus,
            max_hold_minutes=config.maximum_holding_minutes,
            # This field describes when the executable signal is available,
            # not when the original five-minute feature first existed.
            source_feature_available_time_ns=activation_ns,
            source_max_market_time_ns=resolution_ns,
            details=details,
        )
        prior = selected.get(activation_ns)
        if prior is None or signal.score > prior.score:
            selected[activation_ns] = signal

    result = sorted(selected.values(), key=lambda signal: signal.observed_time_ns)
    for signal in result:
        if signal.source_feature_available_time_ns != signal.observed_time_ns:
            raise AssertionError("v142 executable availability mismatch")
        if signal.source_max_market_time_ns >= signal.observed_time_ns:
            raise AssertionError("v142 requires a completed minute after route resolution")
        if signal.observed_time_ns - signal.source_max_market_time_ns != NS_MINUTE:
            raise AssertionError("v142 activation delay is not exactly one minute")
    return result
