"""Pure causal transmission state for immediate notional shocks.

An immediate notional burst identifies an event; it does not prove that the
event transferred price discovery.  This router waits for a strictly later
completed bar and separates four roles:

* cumulative progress beyond the shock close;
* same-side aggressor flow;
* same-side price response and queue support;
* withdrawal of displayed liquidity ahead.

Aggressive flow without cumulative price progress is treated as absorption.
No PnL, trade outcome, or fitted numeric threshold enters this state machine.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from enum import StrEnum
import math


class ShockDecision(StrEnum):
    WAITING = "WAITING"
    CONFIRMED = "CONFIRMED"
    INVALIDATED = "INVALIDATED"
    EXPIRED = "EXPIRED"


def _finite(value: float) -> bool:
    return math.isfinite(float(value))


@dataclass(frozen=True, slots=True)
class ShockTransmission:
    scenario_id: str
    side: int
    shock_index: int
    last_index: int
    expires_index: int
    failure_high: float
    failure_low: float
    parent_extreme: float
    shock_close: float
    observations: int = 0
    latest_cumulative_progress: float = 0.0
    latest_trade_flow: float = 0.0
    latest_trade_return_bps: float = 0.0
    latest_book_support: float = 0.0
    latest_liquidity_ahead_change: float = 0.0
    decision: ShockDecision = ShockDecision.WAITING
    reason: str = "SHOCK_IS_EVENT_NOT_TRANSMISSION"

    def __post_init__(self) -> None:
        if self.side not in (-1, 1):
            raise ValueError("side must be -1 or +1")
        if self.failure_low > self.failure_high:
            raise ValueError("failure bar range is invalid")
        if self.last_index < self.shock_index:
            raise ValueError("last_index cannot precede shock_index")
        if self.expires_index <= self.shock_index:
            raise ValueError("expires_index must be later than shock_index")


@dataclass(frozen=True, slots=True)
class ShockObservation:
    bar_index: int
    high: float
    low: float
    close: float
    flow_60s: float
    ret_60s_bps: float
    depth_imbalance_1: float
    liquidity_ahead_change_1m: float

    def __post_init__(self) -> None:
        if self.low > self.high or not self.low <= self.close <= self.high:
            raise ValueError("shock transmission OHLC values are inconsistent")


def advance_shock_transmission(
    state: ShockTransmission,
    observation: ShockObservation,
) -> ShockTransmission:
    """Advance one strictly later completed observation."""
    if state.decision is not ShockDecision.WAITING:
        raise ValueError("terminal shock transmission cannot be advanced")
    if observation.bar_index <= state.last_index:
        raise ValueError("observations must be strictly later than prior state")
    if observation.bar_index <= state.shock_index:
        raise ValueError("shock bar cannot confirm its own transmission")

    side = state.side
    cumulative_progress = side * (observation.close - state.shock_close)
    trade_flow = side * observation.flow_60s
    trade_return = side * observation.ret_60s_bps
    book_support = side * observation.depth_imbalance_1
    updated = replace(
        state,
        last_index=observation.bar_index,
        observations=state.observations + 1,
        latest_cumulative_progress=cumulative_progress,
        latest_trade_flow=trade_flow,
        latest_trade_return_bps=trade_return,
        latest_book_support=book_support,
        latest_liquidity_ahead_change=observation.liquidity_ahead_change_1m,
    )

    parent_reaccessed = (
        observation.low <= state.parent_extreme
        if side > 0
        else observation.high >= state.parent_extreme
    )
    if parent_reaccessed:
        return replace(
            updated,
            decision=ShockDecision.INVALIDATED,
            reason="PARENT_EXTREME_REACCESSED_AFTER_SHOCK",
        )

    close_back_inside = (
        observation.close <= state.failure_high
        if side > 0
        else observation.close >= state.failure_low
    )
    if close_back_inside:
        return replace(
            updated,
            decision=ShockDecision.INVALIDATED,
            reason="SHOCK_BREAK_FAILED_TO_HOLD_OUTSIDE_FAILURE_BAR",
        )

    # Same-side aggression without net progress is the operational signature of
    # absorption: liquidity consumed the flow instead of transmitting it.
    if (
        _finite(trade_flow)
        and trade_flow > 0.0
        and (
            not _finite(trade_return)
            or trade_return <= 0.0
            or not _finite(cumulative_progress)
            or cumulative_progress <= 0.0
        )
    ):
        return replace(
            updated,
            decision=ShockDecision.INVALIDATED,
            reason="AGGRESSIVE_FLOW_ABSORBED_WITHOUT_PRICE_TRANSMISSION",
        )

    transmitted = (
        _finite(cumulative_progress)
        and _finite(trade_flow)
        and _finite(trade_return)
        and _finite(book_support)
        and _finite(observation.liquidity_ahead_change_1m)
        and cumulative_progress > 0.0
        and trade_flow > 0.0
        and trade_return > 0.0
        and book_support > 0.0
        and observation.liquidity_ahead_change_1m < 0.0
    )
    if transmitted:
        return replace(
            updated,
            decision=ShockDecision.CONFIRMED,
            reason="LATER_PRICE_FLOW_QUEUE_TRANSMISSION_CONFIRMED",
        )

    if observation.bar_index >= state.expires_index:
        return replace(
            updated,
            decision=ShockDecision.EXPIRED,
            reason="SHOCK_DID_NOT_TRANSMIT_WITHIN_REMAINING_CAUSAL_WINDOW",
        )
    return updated


__all__ = [
    "ShockDecision",
    "ShockObservation",
    "ShockTransmission",
    "advance_shock_transmission",
]
