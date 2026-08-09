"""Pure causal displayed-liquidity transition logic for Candidate 16 v2.

The module contains no orders, fills, accounting, or PnL logic.  It separates
three economic roles:

1. a parent attack and completed reclaim define a possible failed auction;
2. displayed liquidity must independently show defense of that boundary;
3. a strictly later completed bar must establish the opposite initiative.

Every comparison is categorical (sign/order/sequence), not fitted to v1 PnL.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from enum import StrEnum
import math


class InitiativeDecision(StrEnum):
    WAITING = "WAITING"
    CONFIRMED = "CONFIRMED"
    INVALIDATED = "INVALIDATED"
    EXPIRED = "EXPIRED"


def _finite(value: float) -> bool:
    return math.isfinite(float(value))


def displayed_failure_supported(
    *,
    parent_direction: int,
    max_reversal_book_support: float,
    max_defending_depth_change: float,
) -> bool:
    """Return whether the book independently defended a reclaimed attack.

    ``max_reversal_book_support`` is already normalized to the prospective
    reversal direction.  ``max_defending_depth_change`` is the largest causal
    change in displayed liquidity on the side standing in front of the attack.
    Both must be strictly positive at least once during the parent episode.
    """
    if parent_direction not in (-1, 1):
        raise ValueError("parent_direction must be -1 or +1")
    return (
        _finite(max_reversal_book_support)
        and _finite(max_defending_depth_change)
        and max_reversal_book_support > 0.0
        and max_defending_depth_change > 0.0
    )


def displayed_acceptance_supported(
    *,
    parent_direction: int,
    max_acceptance_book_support: float,
    min_liquidity_ahead_change: float,
) -> bool:
    """Return whether the book supported true acceptance/price discovery.

    The book must be imbalanced in the attack direction while displayed
    liquidity ahead of that attack withdraws at least once.
    """
    if parent_direction not in (-1, 1):
        raise ValueError("parent_direction must be -1 or +1")
    return (
        _finite(max_acceptance_book_support)
        and _finite(min_liquidity_ahead_change)
        and max_acceptance_book_support > 0.0
        and min_liquidity_ahead_change < 0.0
    )


@dataclass(frozen=True, slots=True)
class FailureLeg:
    scenario_id: str
    side: int
    failure_index: int
    last_index: int
    failure_high: float
    failure_low: float
    parent_extreme: float
    max_wait_bars: int = 3
    observations: int = 0
    latest_trade_flow: float = 0.0
    latest_trade_return_bps: float = 0.0
    latest_trade_book_support: float = 0.0
    latest_liquidity_ahead_change: float = 0.0
    decision: InitiativeDecision = InitiativeDecision.WAITING
    reason: str = "FAILURE_FROZEN_AWAITING_LATER_INITIATIVE"

    def __post_init__(self) -> None:
        if self.side not in (-1, 1):
            raise ValueError("side must be -1 or +1")
        if self.max_wait_bars < 1:
            raise ValueError("max_wait_bars must be positive")
        if self.failure_low > self.failure_high:
            raise ValueError("failure bar range is invalid")
        if self.last_index < self.failure_index:
            raise ValueError("last_index cannot precede failure_index")


@dataclass(frozen=True, slots=True)
class InitiativeObservation:
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
            raise ValueError("initiative OHLC values are inconsistent")


def advance_failure_leg(
    state: FailureLeg,
    observation: InitiativeObservation,
) -> FailureLeg:
    """Advance a frozen failure with one strictly later completed observation."""
    if state.decision is not InitiativeDecision.WAITING:
        raise ValueError("terminal failure leg cannot be advanced")
    if observation.bar_index <= state.last_index:
        raise ValueError("initiative observations must be strictly later")
    if observation.bar_index <= state.failure_index:
        raise ValueError("the failure bar cannot confirm its own initiative")

    side = state.side
    observations = state.observations + 1
    trade_flow = side * observation.flow_60s
    trade_return = side * observation.ret_60s_bps
    trade_book_support = side * observation.depth_imbalance_1

    parent_invalidated = (
        observation.low <= state.parent_extreme
        if side > 0
        else observation.high >= state.parent_extreme
    )
    updated = replace(
        state,
        last_index=observation.bar_index,
        observations=observations,
        latest_trade_flow=trade_flow,
        latest_trade_return_bps=trade_return,
        latest_trade_book_support=trade_book_support,
        latest_liquidity_ahead_change=observation.liquidity_ahead_change_1m,
    )
    if parent_invalidated:
        return replace(
            updated,
            decision=InitiativeDecision.INVALIDATED,
            reason="PARENT_EXTREME_REACCESSED_BEFORE_NEW_INITIATIVE",
        )

    broke_failure_bar = (
        observation.close > state.failure_high
        if side > 0
        else observation.close < state.failure_low
    )
    initiative = (
        broke_failure_bar
        and _finite(trade_flow)
        and _finite(trade_return)
        and _finite(trade_book_support)
        and _finite(observation.liquidity_ahead_change_1m)
        and trade_flow > 0.0
        and trade_return > 0.0
        and trade_book_support > 0.0
        and observation.liquidity_ahead_change_1m < 0.0
    )
    if initiative:
        return replace(
            updated,
            decision=InitiativeDecision.CONFIRMED,
            reason="LATER_PRICE_FLOW_QUEUE_INITIATIVE_CONFIRMED",
        )

    if observations >= state.max_wait_bars:
        return replace(
            updated,
            decision=InitiativeDecision.EXPIRED,
            reason="FAILURE_DID_NOT_PRODUCE_LATER_EXECUTABLE_INITIATIVE",
        )
    return updated


__all__ = [
    "FailureLeg",
    "InitiativeDecision",
    "InitiativeObservation",
    "advance_failure_leg",
    "displayed_acceptance_supported",
    "displayed_failure_supported",
]
