"""Pure causal router for compressed opening-range expansion and first retest.

This module owns no orders, fills, accounting or PnL.  It separates the two
independent economic roles used by Candidate 22:

1. the breakout bar defines price discovery through range escape, efficient
   same-side trade flow, abnormal participation and fresh open-interest growth;
2. a strictly later first retest is confirmed only by displayed-liquidity
   support while the candle body remains outside the opening range.

A first touch which cannot prove defense closes unresolved.  A later second
retest is never substituted for the failed first interaction.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from enum import StrEnum
import math


class ExpansionDecision(StrEnum):
    WAITING = "WAITING"
    CONFIRMED = "CONFIRMED"
    INVALIDATED = "INVALIDATED"
    EXPIRED = "EXPIRED"


def _finite(*values: float) -> bool:
    return all(math.isfinite(float(value)) for value in values)


def directional_close_location(
    *,
    side: int,
    high: float,
    low: float,
    close: float,
) -> float:
    if side not in (-1, 1):
        raise ValueError("side must be -1 or +1")
    span = high - low
    if not _finite(high, low, close) or span <= 0.0 or not low <= close <= high:
        return float("nan")
    return (close - low) / span if side > 0 else (high - close) / span


def expansion_breakout_side(
    *,
    opening_high: float,
    opening_low: float,
    high: float,
    low: float,
    close: float,
    atr: float,
    flow_60s: float,
    ret_60s_bps: float,
    efficiency_60s: float,
    notional_burst: float,
    oi_expanded: bool,
    min_progress_atr: float,
    min_efficiency: float,
    min_close_location: float,
) -> int:
    """Return +1/-1 only for an economically delivered opening-range escape.

    Displayed-book response is deliberately absent here.  It is reserved as
    independent evidence for the later first retest rather than reused to
    confirm the state it helped define.
    """
    if opening_low >= opening_high:
        return 0
    if not oi_expanded:
        return 0
    if not _finite(
        opening_high,
        opening_low,
        high,
        low,
        close,
        atr,
        flow_60s,
        ret_60s_bps,
        efficiency_60s,
        notional_burst,
    ):
        return 0
    if atr <= 0.0 or notional_burst <= 1.0 or efficiency_60s < min_efficiency:
        return 0

    side = 1 if close > opening_high else -1 if close < opening_low else 0
    if side == 0:
        return 0
    boundary = opening_high if side > 0 else opening_low
    progress_atr = side * (close - boundary) / atr
    close_location = directional_close_location(
        side=side,
        high=high,
        low=low,
        close=close,
    )
    if not math.isfinite(close_location):
        return 0
    if progress_atr < min_progress_atr:
        return 0
    if side * flow_60s <= 0.0 or side * ret_60s_bps <= 0.0:
        return 0
    if close_location < min_close_location:
        return 0
    return side


@dataclass(frozen=True, slots=True)
class ExpansionRetest:
    scenario_id: str
    session_key: int
    side: int
    boundary: float
    opposite_boundary: float
    breakout_index: int
    last_index: int
    expires_index: int
    breakout_extreme: float
    max_counterflow: float
    min_close_location: float
    observations: int = 0
    decision: ExpansionDecision = ExpansionDecision.WAITING
    reason: str = "OPENING_RANGE_EXPANSION_AWAITING_FIRST_RETEST"

    def __post_init__(self) -> None:
        if self.side not in (-1, 1):
            raise ValueError("side must be -1 or +1")
        if self.expires_index <= self.breakout_index:
            raise ValueError("retest expiry must follow breakout")
        if self.last_index < self.breakout_index:
            raise ValueError("last_index cannot precede breakout")
        if self.max_counterflow < 0.0:
            raise ValueError("max_counterflow must be non-negative")
        if not 0.0 <= self.min_close_location <= 1.0:
            raise ValueError("min_close_location must be in [0, 1]")
        if self.side > 0 and not self.opposite_boundary < self.boundary:
            raise ValueError("long opening range is invalid")
        if self.side < 0 and not self.boundary < self.opposite_boundary:
            raise ValueError("short opening range is invalid")


@dataclass(frozen=True, slots=True)
class RetestObservation:
    bar_index: int
    high: float
    low: float
    close: float
    flow_15s: float
    depth_imbalance_1: float
    liquidity_ahead_change_1m: float

    def __post_init__(self) -> None:
        if not _finite(
            self.high,
            self.low,
            self.close,
            self.flow_15s,
            self.depth_imbalance_1,
            self.liquidity_ahead_change_1m,
        ):
            raise ValueError("retest observation must be finite")
        if self.low > self.high or not self.low <= self.close <= self.high:
            raise ValueError("retest OHLC values are inconsistent")


def advance_expansion_retest(
    state: ExpansionRetest,
    observation: RetestObservation,
) -> ExpansionRetest:
    """Advance one strictly later bar and resolve the first boundary touch."""
    if state.decision is not ExpansionDecision.WAITING:
        raise ValueError("terminal expansion retest cannot be advanced")
    if observation.bar_index <= state.last_index:
        raise ValueError("retest observations must be strictly later")
    if observation.bar_index <= state.breakout_index:
        raise ValueError("breakout bar cannot retest itself")

    side = state.side
    updated = replace(
        state,
        last_index=observation.bar_index,
        observations=state.observations + 1,
    )

    opposite_broken = (
        observation.low <= state.opposite_boundary
        if side > 0
        else observation.high >= state.opposite_boundary
    )
    if opposite_broken:
        return replace(
            updated,
            decision=ExpansionDecision.INVALIDATED,
            reason="OPENING_RANGE_OPPOSITE_EDGE_REACCESSED",
        )

    body_reentered = (
        observation.close <= state.boundary
        if side > 0
        else observation.close >= state.boundary
    )
    if body_reentered:
        return replace(
            updated,
            decision=ExpansionDecision.INVALIDATED,
            reason="EXPANSION_BODY_FAILED_TO_HOLD_OUTSIDE_RANGE",
        )

    touched = (
        observation.low <= state.boundary
        if side > 0
        else observation.high >= state.boundary
    )
    if touched:
        close_location = directional_close_location(
            side=side,
            high=observation.high,
            low=observation.low,
            close=observation.close,
        )
        defended = (
            math.isfinite(close_location)
            and side * observation.flow_15s >= -state.max_counterflow
            and side * observation.depth_imbalance_1 > 0.0
            and observation.liquidity_ahead_change_1m < 0.0
            and close_location >= state.min_close_location
        )
        if defended:
            return replace(
                updated,
                decision=ExpansionDecision.CONFIRMED,
                reason="FIRST_RETEST_DEFENDED_BY_FLOW_AND_DISPLAYED_LIQUIDITY",
            )
        return replace(
            updated,
            decision=ExpansionDecision.INVALIDATED,
            reason="FIRST_RETEST_TOUCHED_WITHOUT_INDEPENDENT_DEFENSE",
        )

    if observation.bar_index >= state.expires_index:
        return replace(
            updated,
            decision=ExpansionDecision.EXPIRED,
            reason="EXPANSION_DID_NOT_RETEST_BEFORE_WINDOW_EXPIRED",
        )
    return updated


__all__ = [
    "ExpansionDecision",
    "ExpansionRetest",
    "RetestObservation",
    "advance_expansion_retest",
    "directional_close_location",
    "expansion_breakout_side",
]
