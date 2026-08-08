"""Pure causal router for Candidate 17's remembered-liquidity state.

The module does not know about orders, PnL, or NautilusTrader.  It answers one
market question: after displayed liquidity rejected an initial attack, did a
later attack prove that the same defense was depleted rather than replenished?

A depletion confirmation needs independent evidence from price acceptance,
aggressor flow, impact efficiency, displayed-book withdrawal, and expanding
open interest.  Otherwise the state remains unresolved or closes as no-trade.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from enum import StrEnum
import math


class DepletionDecision(StrEnum):
    WAITING = "WAITING"
    CONFIRMED = "CONFIRMED"
    INVALIDATED = "INVALIDATED"
    EXPIRED = "EXPIRED"


def _finite(*values: float) -> bool:
    return all(math.isfinite(float(value)) for value in values)


def clean_first_defense(*, observations: int, outside_closes: int) -> bool:
    """Whether a failed auction completed on the first defended interaction.

    This is categorical, not a fitted score.  Once price has spent a completed
    bar outside the level or required another observation, the episode is no
    longer treated as an immediate failed auction.  It becomes remembered
    defense and must prove either depletion or remain no-trade.
    """
    if observations < 0 or outside_closes < 0:
        raise ValueError("observation counts cannot be negative")
    return observations == 1 and outside_closes == 0


@dataclass(frozen=True, slots=True)
class DefenseMemory:
    scenario_id: str
    direction: int
    defended_level: float
    parent_extreme: float
    atr: float
    created_index: int
    last_index: int
    expires_index: int
    baseline_efficiency: float
    first_defense_change: float
    observations: int = 0
    latest_accepted_distance_atr: float = 0.0
    latest_directional_flow: float = 0.0
    latest_directional_return_bps: float = 0.0
    latest_efficiency: float = 0.0
    latest_book_support: float = 0.0
    latest_defense_change: float = 0.0
    latest_oi_change_5m: float = 0.0
    decision: DepletionDecision = DepletionDecision.WAITING
    reason: str = "REMEMBERED_DEFENSE_AWAITING_QUALITY_REATTACK"

    def __post_init__(self) -> None:
        if self.direction not in (-1, 1):
            raise ValueError("direction must be -1 or +1")
        if not _finite(
            self.defended_level,
            self.parent_extreme,
            self.atr,
            self.baseline_efficiency,
            self.first_defense_change,
        ):
            raise ValueError("defense-memory inputs must be finite")
        if self.atr <= 0.0:
            raise ValueError("atr must be positive")
        if self.created_index < 0:
            raise ValueError("created_index cannot be negative")
        if self.last_index < self.created_index - 1 or self.last_index > self.expires_index:
            raise ValueError("last_index is outside the causal memory window")
        if self.expires_index <= self.created_index:
            raise ValueError("expires_index must be later than created_index")
        if self.first_defense_change <= 0.0:
            raise ValueError("remembered defense must have added displayed liquidity")


@dataclass(frozen=True, slots=True)
class ReattackObservation:
    bar_index: int
    open: float
    high: float
    low: float
    close: float
    flow_60s: float
    ret_60s_bps: float
    efficiency_60s: float
    depth_imbalance_1: float
    defending_depth_change_1m: float
    liquidity_ahead_change_1m: float
    oi_change_5m: float
    positioning_ready: bool
    positioning_age_seconds: float

    def __post_init__(self) -> None:
        if self.low > self.high or not self.low <= self.open <= self.high or not self.low <= self.close <= self.high:
            raise ValueError("reattack OHLC values are inconsistent")


def advance_defense_memory(
    state: DefenseMemory,
    observation: ReattackObservation,
    *,
    max_positioning_age_seconds: float = 300.0,
) -> DefenseMemory:
    """Advance remembered defense by one completed, causally available bar."""
    if state.decision is not DepletionDecision.WAITING:
        raise ValueError("terminal defense memory cannot be advanced")
    if observation.bar_index <= state.last_index:
        raise ValueError("reattack observations must be strictly later")
    if max_positioning_age_seconds < 0.0:
        raise ValueError("max_positioning_age_seconds cannot be negative")

    direction = state.direction
    observations = state.observations + 1
    accepted_distance = direction * (observation.close - state.parent_extreme) / state.atr
    directional_body = direction * (observation.close - observation.open) / state.atr
    directional_flow = direction * observation.flow_60s
    directional_return = direction * observation.ret_60s_bps
    book_support = direction * observation.depth_imbalance_1
    positioning_fresh = (
        observation.positioning_ready
        and _finite(observation.positioning_age_seconds)
        and 0.0 <= observation.positioning_age_seconds <= max_positioning_age_seconds
    )

    updated = replace(
        state,
        last_index=observation.bar_index,
        observations=observations,
        latest_accepted_distance_atr=accepted_distance,
        latest_directional_flow=directional_flow,
        latest_directional_return_bps=directional_return,
        latest_efficiency=observation.efficiency_60s,
        latest_book_support=book_support,
        latest_defense_change=observation.defending_depth_change_1m,
        latest_oi_change_5m=observation.oi_change_5m,
    )

    evidence_finite = _finite(
        accepted_distance,
        directional_body,
        directional_flow,
        directional_return,
        observation.efficiency_60s,
        book_support,
        observation.defending_depth_change_1m,
        observation.liquidity_ahead_change_1m,
        observation.oi_change_5m,
    )
    confirmed = (
        evidence_finite
        and accepted_distance > 0.0
        and directional_body > 0.0
        and directional_flow > 0.0
        and directional_return > 0.0
        and observation.efficiency_60s > max(state.baseline_efficiency, 0.0)
        and book_support > 0.0
        and observation.defending_depth_change_1m < state.first_defense_change
        and observation.liquidity_ahead_change_1m < 0.0
        and positioning_fresh
        and observation.oi_change_5m > 0.0
    )
    if confirmed:
        return replace(
            updated,
            decision=DepletionDecision.CONFIRMED,
            reason=(
                "REATTACK_ACCEPTED_WITH_STRONGER_IMPACT_WEAKER_DEFENSE_"
                "AND_EXPANDING_OPEN_INTEREST"
            ),
        )

    # A completed close back through the original defended level means the
    # remembered defense still controls the auction.  That is not permission to
    # reverse here: the prior reversal branch was already suppressed, so this
    # episode closes as unresolved/no-trade.
    defense_held = direction * (observation.close - state.defended_level) < 0.0
    if defense_held:
        return replace(
            updated,
            decision=DepletionDecision.INVALIDATED,
            reason="REMEMBERED_DEFENSE_HELD_AND_PRICE_RETURNED_INSIDE",
        )

    if observation.bar_index >= state.expires_index:
        return replace(
            updated,
            decision=DepletionDecision.EXPIRED,
            reason="REATTACK_DID_NOT_PROVE_DEFENSE_DEPLETION_IN_TIME",
        )
    return updated


__all__ = [
    "DefenseMemory",
    "DepletionDecision",
    "ReattackObservation",
    "advance_defense_memory",
    "clean_first_defense",
]
