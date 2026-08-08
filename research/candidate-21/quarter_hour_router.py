"""Pure causal router for quarter-hour auction events.

A large first-10-second order-flow burst at a 15-minute boundary is an event,
not an entry.  The router waits for strictly later completed bars and asks
whether the event transferred price discovery (acceptance), was absorbed and
reclaimed (failed auction), or remained ambiguous (no trade).

This module has no NautilusTrader dependency and never uses PnL or future data.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from enum import StrEnum
import math


class ClockDecision(StrEnum):
    WAITING = "WAITING"
    ACCEPTANCE = "ACCEPTANCE"
    FAILED_AUCTION = "FAILED_AUCTION"
    INVALIDATED = "INVALIDATED"
    UNRESOLVED = "UNRESOLVED"


def _finite(value: float) -> bool:
    return math.isfinite(float(value))


def directional_close_location(
    *,
    direction: int,
    high: float,
    low: float,
    close: float,
) -> float:
    """Return close location in the event direction, bounded to [0, 1]."""
    if direction not in (-1, 1):
        raise ValueError("direction must be -1 or +1")
    if low > high or not low <= close <= high:
        raise ValueError("OHLC values are inconsistent")
    span = max(high - low, 1e-12)
    raw = (close - low) / span if direction > 0 else (high - close) / span
    return min(1.0, max(0.0, raw))


@dataclass(frozen=True, slots=True)
class ClockThresholds:
    max_wait_bars: int = 3
    acceptance_min_progress_atr: float = 0.18
    acceptance_min_flow: float = 0.06
    acceptance_min_efficiency: float = 0.30
    acceptance_min_close_location: float = 0.56
    failure_reentry_atr: float = 0.02
    failure_max_event_efficiency: float = 0.45
    failure_max_extension_atr: float = 0.30
    failure_min_reverse_flow: float = 0.04
    failure_min_reverse_efficiency: float = 0.20
    failure_min_close_location: float = 0.56

    def __post_init__(self) -> None:
        if self.max_wait_bars < 1:
            raise ValueError("max_wait_bars must be positive")
        nonnegative = (
            self.acceptance_min_progress_atr,
            self.acceptance_min_flow,
            self.acceptance_min_efficiency,
            self.acceptance_min_close_location,
            self.failure_reentry_atr,
            self.failure_max_event_efficiency,
            self.failure_max_extension_atr,
            self.failure_min_reverse_flow,
            self.failure_min_reverse_efficiency,
            self.failure_min_close_location,
        )
        if any(not _finite(value) or value < 0.0 for value in nonnegative):
            raise ValueError("router thresholds must be finite and nonnegative")
        for value in (
            self.acceptance_min_efficiency,
            self.acceptance_min_close_location,
            self.failure_max_event_efficiency,
            self.failure_min_reverse_efficiency,
            self.failure_min_close_location,
        ):
            if value > 1.0:
                raise ValueError("efficiency/location thresholds cannot exceed one")


@dataclass(frozen=True, slots=True)
class ClockAuction:
    scenario_id: str
    direction: int
    boundary_index: int
    last_index: int
    expires_index: int
    boundary_level: float
    range_opposite: float
    acceptance_target: float
    rejection_target: float
    atr: float
    event_high: float
    event_low: float
    event_close: float
    event_open_flow: float
    event_phase_burst: float
    event_efficiency: float
    event_extension_atr: float
    observations: int = 0
    latest_progress_atr: float = 0.0
    latest_directional_flow: float = 0.0
    latest_directional_return_bps: float = 0.0
    latest_efficiency: float = 0.0
    latest_directional_close_location: float = 0.0
    latest_book_support: float = 0.0
    latest_liquidity_ahead_change: float = 0.0
    max_extension_atr: float = 0.0
    decision: ClockDecision = ClockDecision.WAITING
    reason: str = "CLOCK_EVENT_AWAITS_LATER_AUCTION_RESPONSE"

    def __post_init__(self) -> None:
        if self.direction not in (-1, 1):
            raise ValueError("direction must be -1 or +1")
        if self.last_index < self.boundary_index:
            raise ValueError("last_index cannot precede boundary_index")
        if self.expires_index <= self.boundary_index:
            raise ValueError("expires_index must follow boundary_index")
        if self.atr <= 0.0 or not _finite(self.atr):
            raise ValueError("atr must be finite and positive")
        if self.event_low > self.event_high:
            raise ValueError("event range is invalid")
        if not self.event_low <= self.event_close <= self.event_high:
            raise ValueError("event close is outside event range")
        if self.boundary_level <= 0.0 or self.range_opposite <= 0.0:
            raise ValueError("auction levels must be positive")
        if self.acceptance_target <= 0.0 or self.rejection_target <= 0.0:
            raise ValueError("targets must be positive")
        if self.direction > 0:
            if not self.range_opposite < self.boundary_level < self.acceptance_target:
                raise ValueError("long-direction balance geometry is invalid")
            if self.rejection_target != self.range_opposite:
                raise ValueError("rejection target must be the opposite balance edge")
        else:
            if not self.acceptance_target < self.boundary_level < self.range_opposite:
                raise ValueError("short-direction balance geometry is invalid")
            if self.rejection_target != self.range_opposite:
                raise ValueError("rejection target must be the opposite balance edge")


@dataclass(frozen=True, slots=True)
class ClockObservation:
    bar_index: int
    high: float
    low: float
    close: float
    flow_60s: float
    ret_60s_bps: float
    efficiency_60s: float
    depth_imbalance_1: float
    liquidity_ahead_change_1m: float

    def __post_init__(self) -> None:
        if self.low > self.high or not self.low <= self.close <= self.high:
            raise ValueError("observation OHLC values are inconsistent")


def advance_clock_auction(
    state: ClockAuction,
    observation: ClockObservation,
    thresholds: ClockThresholds,
) -> ClockAuction:
    """Advance a quarter-hour event with one strictly later completed bar."""
    if state.decision is not ClockDecision.WAITING:
        raise ValueError("terminal auction state cannot be advanced")
    if observation.bar_index <= state.last_index:
        raise ValueError("observations must be strictly increasing")
    if observation.bar_index <= state.boundary_index:
        raise ValueError("boundary bar cannot confirm its own event")

    direction = state.direction
    progress_atr = direction * (observation.close - state.boundary_level) / state.atr
    extreme = observation.high if direction > 0 else observation.low
    extension_atr = direction * (extreme - state.boundary_level) / state.atr
    directional_flow = direction * observation.flow_60s
    directional_return = direction * observation.ret_60s_bps
    book_support = direction * observation.depth_imbalance_1
    close_location = directional_close_location(
        direction=direction,
        high=observation.high,
        low=observation.low,
        close=observation.close,
    )
    updated = replace(
        state,
        last_index=observation.bar_index,
        observations=state.observations + 1,
        latest_progress_atr=progress_atr,
        latest_directional_flow=directional_flow,
        latest_directional_return_bps=directional_return,
        latest_efficiency=observation.efficiency_60s,
        latest_directional_close_location=close_location,
        latest_book_support=book_support,
        latest_liquidity_ahead_change=observation.liquidity_ahead_change_1m,
        max_extension_atr=max(state.max_extension_atr, extension_atr),
    )

    # A target touched before a decision means the tradeable geometry was
    # consumed before entry.  The event may have been directionally correct,
    # but it is no longer a valid new trade.
    acceptance_target_touched = (
        observation.high >= state.acceptance_target
        if direction > 0
        else observation.low <= state.acceptance_target
    )
    rejection_target_touched = (
        observation.low <= state.rejection_target
        if direction > 0
        else observation.high >= state.rejection_target
    )
    if acceptance_target_touched or rejection_target_touched:
        return replace(
            updated,
            decision=ClockDecision.INVALIDATED,
            reason="NATURAL_TARGET_CONSUMED_BEFORE_CAUSAL_CONFIRMATION",
        )

    extended_beyond_event_close = direction * (observation.close - state.event_close) > 0.0
    held_outside = progress_atr > 0.0
    acceptance = (
        extended_beyond_event_close
        and held_outside
        and progress_atr >= thresholds.acceptance_min_progress_atr
        and _finite(directional_flow)
        and directional_flow >= thresholds.acceptance_min_flow
        and _finite(directional_return)
        and directional_return > 0.0
        and _finite(observation.efficiency_60s)
        and observation.efficiency_60s >= thresholds.acceptance_min_efficiency
        and close_location >= thresholds.acceptance_min_close_location
        and _finite(book_support)
        and book_support > 0.0
        and _finite(observation.liquidity_ahead_change_1m)
        and observation.liquidity_ahead_change_1m < 0.0
    )
    if acceptance:
        return replace(
            updated,
            decision=ClockDecision.ACCEPTANCE,
            reason="LATER_PRICE_FLOW_AND_LIQUIDITY_TRANSMISSION_CONFIRMED",
        )

    opposite_location = directional_close_location(
        direction=-direction,
        high=observation.high,
        low=observation.low,
        close=observation.close,
    )
    reentered_balance = progress_atr <= thresholds.failure_reentry_atr
    reverse_flow = (
        _finite(directional_flow)
        and directional_flow <= -thresholds.failure_min_reverse_flow
    )
    reverse_return = _finite(directional_return) and directional_return < 0.0
    reverse_efficiency = (
        _finite(observation.efficiency_60s)
        and observation.efficiency_60s >= thresholds.failure_min_reverse_efficiency
    )
    opposite_close = opposite_location >= thresholds.failure_min_close_location
    displayed_defense = (
        _finite(book_support)
        and book_support < 0.0
        and _finite(observation.liquidity_ahead_change_1m)
        and observation.liquidity_ahead_change_1m >= 0.0
    )
    event_was_absorbed = (
        state.event_efficiency <= thresholds.failure_max_event_efficiency
        or max(state.event_extension_atr, updated.max_extension_atr)
        <= thresholds.failure_max_extension_atr
    )
    failed = (
        reentered_balance
        and reverse_flow
        and reverse_return
        and reverse_efficiency
        and opposite_close
        and displayed_defense
        and event_was_absorbed
    )
    if failed:
        return replace(
            updated,
            decision=ClockDecision.FAILED_AUCTION,
            reason="BOUNDARY_FLOW_ABSORBED_RECLAIMED_AND_REVERSED",
        )

    if observation.bar_index >= state.expires_index:
        return replace(
            updated,
            decision=ClockDecision.UNRESOLVED,
            reason="CLOCK_EVENT_REMAINED_MIXED_WITHIN_CAUSAL_WINDOW",
        )
    return updated


__all__ = [
    "ClockAuction",
    "ClockDecision",
    "ClockObservation",
    "ClockThresholds",
    "advance_clock_auction",
    "directional_close_location",
]
