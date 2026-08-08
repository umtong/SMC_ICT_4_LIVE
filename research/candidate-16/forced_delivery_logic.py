"""Pure causal router for forced-deleveraging continuation.

The event and its confirmation deliberately use different information:

* event/context: a completed five-minute price shock, simultaneous open-interest
  contraction, and perpetual-premium displacement in the shock direction;
* transition: a later completed minute with persistent aggressor flow, efficient
  price progress and withdrawal of displayed liquidity ahead;
* trigger: the later minute's directional body and close location.

There is no reversal branch. If delivery does not persist, the event terminates
as UNRESOLVED/NO_TRADE.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from enum import StrEnum
import math


class DeliveryDecision(StrEnum):
    PENDING = "PENDING"
    CONTINUATION = "CONTINUATION"
    UNRESOLVED = "UNRESOLVED"


@dataclass(frozen=True, slots=True)
class DeliveryConfig:
    minimum_event_move_atr: float = 1.0
    maximum_event_oi_change_5m: float = -0.00075
    minimum_event_premium_dislocation: float = 0.00002
    maximum_confirmation_bars: int = 3
    minimum_confirmation_flow: float = 0.10
    minimum_confirmation_efficiency: float = 0.20
    minimum_ahead_depth_withdrawal: float = 0.0
    minimum_trigger_body_atr: float = 0.08
    minimum_trigger_close_location: float = 0.55

    def __post_init__(self) -> None:
        if self.minimum_event_move_atr <= 0.0:
            raise ValueError("minimum_event_move_atr must be positive")
        if self.maximum_event_oi_change_5m >= 0.0:
            raise ValueError("maximum_event_oi_change_5m must be negative")
        if self.minimum_event_premium_dislocation < 0.0:
            raise ValueError("minimum_event_premium_dislocation must be non-negative")
        if self.maximum_confirmation_bars < 1:
            raise ValueError("maximum_confirmation_bars must be positive")


@dataclass(frozen=True, slots=True)
class ForcedDeliveryEvent:
    scenario_id: str
    direction: int
    created_index: int
    last_index: int
    event_start_price: float
    event_close: float
    event_high: float
    event_low: float
    atr: float
    move_atr: float
    oi_change_5m: float
    premium_change_5m: float
    observations: int = 0
    decision: DeliveryDecision = DeliveryDecision.PENDING
    reason: str = "FORCED_DELEVERAGING_EVENT_OPEN"

    def __post_init__(self) -> None:
        if self.direction not in (-1, 1):
            raise ValueError("direction must be -1 or +1")
        if self.atr <= 0.0 or not math.isfinite(self.atr):
            raise ValueError("atr must be finite and positive")
        if not (0.0 < self.event_low <= self.event_high):
            raise ValueError("event prices are invalid")


@dataclass(frozen=True, slots=True)
class DeliveryObservation:
    bar_index: int
    open: float
    high: float
    low: float
    close: float
    atr: float
    flow_60s: float
    efficiency_60s: float
    bid_depth_change_1m: float
    ask_depth_change_1m: float

    def __post_init__(self) -> None:
        if self.bar_index < 0:
            raise ValueError("bar_index must be non-negative")
        if self.atr <= 0.0 or not math.isfinite(self.atr):
            raise ValueError("atr must be finite and positive")
        if not (
            0.0
            < self.low
            <= min(self.open, self.close)
            <= max(self.open, self.close)
            <= self.high
        ):
            raise ValueError("OHLC values are inconsistent")


def _finite(value: float) -> bool:
    return math.isfinite(float(value))


def detect_forced_delivery_event(
    *,
    scenario_id: str,
    bar_index: int,
    event_start_price: float,
    event_close: float,
    event_high: float,
    event_low: float,
    atr: float,
    oi_change_5m: float,
    premium_change_5m: float,
    config: DeliveryConfig,
) -> ForcedDeliveryEvent | None:
    """Open one event from price, OI and premium only.

    Aggressor flow and displayed depth are intentionally absent from this
    signature so they remain independent transition evidence.
    """
    values = (
        event_start_price,
        event_close,
        event_high,
        event_low,
        atr,
        oi_change_5m,
        premium_change_5m,
    )
    if not all(_finite(value) for value in values) or atr <= 0.0:
        return None
    raw_move = event_close - event_start_price
    if raw_move == 0.0:
        return None
    direction = 1 if raw_move > 0.0 else -1
    move_atr = abs(raw_move) / atr
    if move_atr < config.minimum_event_move_atr:
        return None
    if oi_change_5m > config.maximum_event_oi_change_5m:
        return None
    if direction * premium_change_5m < config.minimum_event_premium_dislocation:
        return None
    return ForcedDeliveryEvent(
        scenario_id=scenario_id,
        direction=direction,
        created_index=bar_index,
        last_index=bar_index,
        event_start_price=event_start_price,
        event_close=event_close,
        event_high=event_high,
        event_low=event_low,
        atr=atr,
        move_atr=move_atr,
        oi_change_5m=oi_change_5m,
        premium_change_5m=premium_change_5m,
    )


def advance_forced_delivery(
    event: ForcedDeliveryEvent,
    observation: DeliveryObservation,
    config: DeliveryConfig,
) -> ForcedDeliveryEvent:
    """Advance with a strictly later completed minute."""
    if event.decision is not DeliveryDecision.PENDING:
        raise ValueError("terminal event cannot be advanced")
    if observation.bar_index <= event.last_index:
        raise ValueError("confirmation must be later than the event")

    side = event.direction
    observations = event.observations + 1
    directional_flow = side * observation.flow_60s
    directional_body = side * (observation.close - observation.open) / observation.atr
    span = max(observation.high - observation.low, 1e-12)
    close_location = (
        (observation.close - observation.low) / span
        if side > 0
        else (observation.high - observation.close) / span
    )
    ahead_change = (
        observation.ask_depth_change_1m
        if side > 0
        else observation.bid_depth_change_1m
    )
    updated = replace(
        event,
        last_index=observation.bar_index,
        observations=observations,
        reason="WAITING_FOR_PERSISTENT_FORCED_DELIVERY",
    )

    continuation = (
        all(
            _finite(value)
            for value in (
                directional_flow,
                observation.efficiency_60s,
                directional_body,
                close_location,
                ahead_change,
            )
        )
        and directional_flow >= config.minimum_confirmation_flow
        and observation.efficiency_60s >= config.minimum_confirmation_efficiency
        and ahead_change < -config.minimum_ahead_depth_withdrawal
        and directional_body >= config.minimum_trigger_body_atr
        and close_location >= config.minimum_trigger_close_location
    )
    if continuation:
        return replace(
            updated,
            decision=DeliveryDecision.CONTINUATION,
            reason=(
                "OI_COLLAPSE_FOLLOWED_BY_FLOW_EFFICIENCY_AND_"
                "AHEAD_DEPTH_WITHDRAWAL"
            ),
        )

    event_invalidated = (
        observation.close <= event.event_start_price
        if side > 0
        else observation.close >= event.event_start_price
    )
    if event_invalidated or observations >= config.maximum_confirmation_bars:
        return replace(
            updated,
            decision=DeliveryDecision.UNRESOLVED,
            reason=(
                "EVENT_ORIGIN_REENTERED_BEFORE_PERSISTENT_DELIVERY"
                if event_invalidated
                else "DELIVERY_CONFIRMATION_WINDOW_EXPIRED"
            ),
        )
    return updated


__all__ = [
    "DeliveryConfig",
    "DeliveryDecision",
    "DeliveryObservation",
    "ForcedDeliveryEvent",
    "advance_forced_delivery",
    "detect_forced_delivery_event",
]
