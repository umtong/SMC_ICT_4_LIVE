"""Pure causal first-retest state for a completed failed-auction initiative."""
from __future__ import annotations

from dataclasses import dataclass, replace
from enum import StrEnum
import math


class RetestDecision(StrEnum):
    WAITING = "WAITING"
    CONFIRMED = "CONFIRMED"
    INVALIDATED = "INVALIDATED"
    EXPIRED = "EXPIRED"


@dataclass(frozen=True, slots=True)
class FailureRetest:
    scenario_id: str
    side: int
    boundary: float
    parent_extreme: float
    atr: float
    created_index: int
    last_index: int
    expires_index: int
    touched: bool = False
    latest_directional_flow: float = 0.0
    latest_book_support: float = 0.0
    latest_close_location: float = 0.0
    decision: RetestDecision = RetestDecision.WAITING
    reason: str = "INITIATIVE_CONFIRMED_AWAITING_FIRST_RETEST"

    def __post_init__(self) -> None:
        if self.side not in (-1, 1):
            raise ValueError("side must be -1 or +1")
        if not all(math.isfinite(float(v)) for v in (self.boundary, self.parent_extreme, self.atr)):
            raise ValueError("retest prices must be finite")
        if self.atr <= 0.0:
            raise ValueError("atr must be positive")
        if self.last_index < self.created_index or self.expires_index <= self.created_index:
            raise ValueError("invalid causal retest window")


@dataclass(frozen=True, slots=True)
class RetestObservation:
    bar_index: int
    open: float
    high: float
    low: float
    close: float
    flow_15s: float
    depth_imbalance_1: float

    def __post_init__(self) -> None:
        if self.low > self.high or not self.low <= self.open <= self.high or not self.low <= self.close <= self.high:
            raise ValueError("retest OHLC values are inconsistent")


def advance_failure_retest(
    state: FailureRetest,
    observation: RetestObservation,
    *,
    touch_tolerance_atr: float,
    max_counterflow: float,
    min_close_location: float,
) -> FailureRetest:
    """Evaluate only the first touch after initiative; no repeated rescue attempts."""
    if state.decision is not RetestDecision.WAITING:
        raise ValueError("terminal retest cannot be advanced")
    if observation.bar_index <= state.last_index:
        raise ValueError("retest observations must be strictly later")
    if touch_tolerance_atr < 0.0 or max_counterflow < 0.0:
        raise ValueError("tolerances cannot be negative")
    if not 0.0 <= min_close_location <= 1.0:
        raise ValueError("min_close_location must be in [0, 1]")

    side = state.side
    parent_invalidated = (
        observation.low <= state.parent_extreme
        if side > 0
        else observation.high >= state.parent_extreme
    )
    if parent_invalidated:
        return replace(
            state,
            last_index=observation.bar_index,
            decision=RetestDecision.INVALIDATED,
            reason="PARENT_EXTREME_REACCESSED_ON_RETEST",
        )

    touched = (
        observation.low <= state.boundary + touch_tolerance_atr * state.atr
        if side > 0
        else observation.high >= state.boundary - touch_tolerance_atr * state.atr
    )
    if not touched:
        if observation.bar_index >= state.expires_index:
            return replace(
                state,
                last_index=observation.bar_index,
                decision=RetestDecision.EXPIRED,
                reason="FIRST_RETEST_DID_NOT_OCCUR_BEFORE_PRICE_DISCOVERY_MOVED_ON",
            )
        return replace(state, last_index=observation.bar_index)

    directional_flow = side * observation.flow_15s
    book_support = side * observation.depth_imbalance_1
    directional_body = side * (observation.close - observation.open)
    closed_beyond_boundary = side * (observation.close - state.boundary) > 0.0
    span = max(observation.high - observation.low, 1e-12)
    close_location = (
        (observation.close - observation.low) / span
        if side > 0
        else (observation.high - observation.close) / span
    )
    finite = all(
        math.isfinite(float(v))
        for v in (directional_flow, book_support, directional_body, close_location)
    )
    confirmed = (
        finite
        and closed_beyond_boundary
        and directional_body > 0.0
        and directional_flow >= -max_counterflow
        and book_support > 0.0
        and close_location >= min_close_location
    )
    if confirmed:
        return replace(
            state,
            last_index=observation.bar_index,
            touched=True,
            latest_directional_flow=directional_flow,
            latest_book_support=book_support,
            latest_close_location=close_location,
            decision=RetestDecision.CONFIRMED,
            reason="FIRST_RETEST_HELD_WITH_DIRECTIONAL_CLOSE_AND_BOOK_SUPPORT",
        )
    return replace(
        state,
        last_index=observation.bar_index,
        touched=True,
        latest_directional_flow=directional_flow,
        latest_book_support=book_support,
        latest_close_location=close_location,
        decision=RetestDecision.INVALIDATED,
        reason="FIRST_RETEST_TOUCHED_BUT_DID_NOT_HOLD",
    )


__all__ = [
    "FailureRetest",
    "RetestDecision",
    "RetestObservation",
    "advance_failure_retest",
]
