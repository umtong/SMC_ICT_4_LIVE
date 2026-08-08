"""Pure causal state machine for forced-flow exhaustion reversals.

A directional price/flow impulse with contracting open interest and an extending
perpetual premium is treated as a possible forced-position cascade, not an
entry. A trade exists only after a strictly later bar shows that same-side
aggression no longer advances price and a still later bar proves an opposite
reprice while the natural pre-shock objective remains unconsumed.

This module contains no NautilusTrader dependency, PnL calculation, order
matching, or outcome-fitted threshold.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from enum import StrEnum
import math


class ForcedDecision(StrEnum):
    WAITING_EXHAUSTION = "WAITING_EXHAUSTION"
    WAITING_REVERSAL = "WAITING_REVERSAL"
    CONFIRMED = "CONFIRMED"
    INVALIDATED = "INVALIDATED"
    EXPIRED = "EXPIRED"


def _finite(value: float) -> bool:
    return math.isfinite(float(value))


@dataclass(frozen=True, slots=True)
class ForcedShockThresholds:
    min_move_atr: float = 1.25
    min_notional_burst: float = 1.50
    min_directional_flow: float = 1.0 / 3.0
    min_event_efficiency: float = 0.45

    def __post_init__(self) -> None:
        values = (
            self.min_move_atr,
            self.min_notional_burst,
            self.min_directional_flow,
            self.min_event_efficiency,
        )
        if any(not _finite(value) or value <= 0.0 for value in values):
            raise ValueError("forced-shock thresholds must be finite and positive")
        if self.min_directional_flow > 1.0 or self.min_event_efficiency > 1.0:
            raise ValueError("flow and efficiency thresholds cannot exceed one")


@dataclass(frozen=True, slots=True)
class ForcedShockEvidence:
    move_atr: float
    notional_burst: float
    flow_3m: float
    efficiency_60s: float
    oi_change_15m: float
    premium_change_5m: float


def classify_forced_shock(
    evidence: ForcedShockEvidence,
    thresholds: ForcedShockThresholds,
) -> int:
    """Return the cascade direction, or zero when the event is unresolved."""
    values = (
        evidence.move_atr,
        evidence.notional_burst,
        evidence.flow_3m,
        evidence.efficiency_60s,
        evidence.oi_change_15m,
        evidence.premium_change_5m,
    )
    if not all(_finite(value) for value in values):
        return 0
    if evidence.move_atr == 0.0:
        return 0
    direction = 1 if evidence.move_atr > 0.0 else -1
    return direction if (
        abs(evidence.move_atr) >= thresholds.min_move_atr
        and evidence.notional_burst >= thresholds.min_notional_burst
        and direction * evidence.flow_3m >= thresholds.min_directional_flow
        and evidence.efficiency_60s >= thresholds.min_event_efficiency
        and evidence.oi_change_15m < 0.0
        and direction * evidence.premium_change_5m > 0.0
    ) else 0


@dataclass(frozen=True, slots=True)
class ForcedResponseThresholds:
    max_wait_bars: int = 6
    min_retrace_fraction: float = 1.0 / 3.0
    min_reverse_flow: float = 0.10
    min_reverse_efficiency: float = 0.20

    def __post_init__(self) -> None:
        if self.max_wait_bars < 2:
            raise ValueError("max_wait_bars must allow separate exhaustion and reversal")
        values = (
            self.min_retrace_fraction,
            self.min_reverse_flow,
            self.min_reverse_efficiency,
        )
        if any(not _finite(value) or value <= 0.0 for value in values):
            raise ValueError("response thresholds must be finite and positive")
        if any(value > 1.0 for value in values):
            raise ValueError("fractional response thresholds cannot exceed one")


@dataclass(frozen=True, slots=True)
class ForcedEpisode:
    scenario_id: str
    shock_direction: int
    shock_index: int
    last_index: int
    expires_index: int
    origin_price: float
    event_high: float
    event_low: float
    event_close: float
    atr: float
    event_efficiency: float
    event_oi_change_15m: float
    event_premium_change_5m: float
    event_notional_burst: float
    event_flow_3m: float
    latest_high: float
    latest_low: float
    observations: int = 0
    exhaustion_index: int = -1
    latest_retrace_fraction: float = 0.0
    latest_same_side_flow: float = 0.0
    latest_reverse_flow: float = 0.0
    latest_book_support: float = 0.0
    latest_defending_depth_change: float = 0.0
    decision: ForcedDecision = ForcedDecision.WAITING_EXHAUSTION
    reason: str = "FORCED_FLOW_EVENT_AWAITS_LATER_EXHAUSTION"

    def __post_init__(self) -> None:
        if self.shock_direction not in (-1, 1):
            raise ValueError("shock_direction must be -1 or +1")
        if self.last_index < self.shock_index:
            raise ValueError("last_index cannot precede shock_index")
        if self.expires_index <= self.shock_index:
            raise ValueError("expires_index must follow shock_index")
        if not _finite(self.atr) or self.atr <= 0.0:
            raise ValueError("atr must be finite and positive")
        if self.event_low > self.event_high:
            raise ValueError("event range is invalid")
        if not self.event_low <= self.event_close <= self.event_high:
            raise ValueError("event close lies outside event range")
        if self.origin_price <= 0.0:
            raise ValueError("origin_price must be positive")
        if self.shock_direction > 0 and not self.origin_price < self.event_high:
            raise ValueError("up-shock origin must precede the event high")
        if self.shock_direction < 0 and not self.event_low < self.origin_price:
            raise ValueError("down-shock origin must precede the event low")


@dataclass(frozen=True, slots=True)
class ForcedObservation:
    bar_index: int
    high: float
    low: float
    close: float
    flow_60s: float
    flow_3m: float
    ret_60s_bps: float
    efficiency_60s: float
    depth_imbalance_1: float
    defending_depth_change_1m: float
    oi_change_15m: float
    premium_change_1m: float

    def __post_init__(self) -> None:
        if self.low > self.high or not self.low <= self.close <= self.high:
            raise ValueError("observation OHLC values are inconsistent")


def advance_forced_episode(
    state: ForcedEpisode,
    observation: ForcedObservation,
    thresholds: ForcedResponseThresholds,
) -> ForcedEpisode:
    """Advance one strictly later completed observation."""
    if state.decision not in (
        ForcedDecision.WAITING_EXHAUSTION,
        ForcedDecision.WAITING_REVERSAL,
    ):
        raise ValueError("terminal forced-flow state cannot be advanced")
    if observation.bar_index <= state.last_index:
        raise ValueError("observations must be strictly increasing")
    if observation.bar_index <= state.shock_index:
        raise ValueError("shock bar cannot confirm its own state")

    direction = state.shock_direction
    reversal_side = -direction
    latest_high = max(state.latest_high, observation.high)
    latest_low = min(state.latest_low, observation.low)
    extreme = latest_high if direction > 0 else latest_low
    leg_distance = abs(state.origin_price - extreme)
    retrace_fraction = (
        reversal_side * (observation.close - extreme) / leg_distance
        if leg_distance > 0.0
        else 0.0
    )
    same_side_flow = direction * observation.flow_60s
    same_side_return = direction * observation.ret_60s_bps
    reverse_flow = reversal_side * observation.flow_60s
    reverse_flow_3m = reversal_side * observation.flow_3m
    book_support = reversal_side * observation.depth_imbalance_1

    updated = replace(
        state,
        last_index=observation.bar_index,
        latest_high=latest_high,
        latest_low=latest_low,
        observations=state.observations + 1,
        latest_retrace_fraction=retrace_fraction,
        latest_same_side_flow=same_side_flow,
        latest_reverse_flow=reverse_flow,
        latest_book_support=book_support,
        latest_defending_depth_change=observation.defending_depth_change_1m,
    )

    target_touched = (
        observation.low <= state.origin_price
        if direction > 0
        else observation.high >= state.origin_price
    )
    if target_touched:
        return replace(
            updated,
            decision=ForcedDecision.INVALIDATED,
            reason="PRE_SHOCK_OBJECTIVE_CONSUMED_BEFORE_ENTRY",
        )

    if state.decision is ForcedDecision.WAITING_EXHAUSTION:
        exhausted = (
            _finite(same_side_flow)
            and same_side_flow > 0.0
            and (
                not _finite(same_side_return)
                or same_side_return <= 0.0
                or not _finite(observation.efficiency_60s)
                or observation.efficiency_60s < state.event_efficiency
            )
            and _finite(book_support)
            and book_support > 0.0
            and _finite(observation.defending_depth_change_1m)
            and observation.defending_depth_change_1m >= 0.0
            and _finite(observation.oi_change_15m)
            and observation.oi_change_15m <= 0.0
        )
        if exhausted:
            return replace(
                updated,
                exhaustion_index=observation.bar_index,
                decision=ForcedDecision.WAITING_REVERSAL,
                reason="AGGRESSIVE_FORCED_FLOW_ABSORBED_BY_DEFENDING_LIQUIDITY",
            )
    else:
        confirmed = (
            observation.bar_index > state.exhaustion_index
            and retrace_fraction >= thresholds.min_retrace_fraction
            and _finite(reverse_flow)
            and reverse_flow >= thresholds.min_reverse_flow
            and _finite(reverse_flow_3m)
            and reverse_flow_3m > 0.0
            and _finite(observation.ret_60s_bps)
            and reversal_side * observation.ret_60s_bps > 0.0
            and _finite(observation.efficiency_60s)
            and observation.efficiency_60s >= thresholds.min_reverse_efficiency
            and _finite(book_support)
            and book_support > 0.0
            and _finite(observation.defending_depth_change_1m)
            and observation.defending_depth_change_1m >= 0.0
            and _finite(observation.oi_change_15m)
            and observation.oi_change_15m <= 0.0
            and _finite(observation.premium_change_1m)
            and reversal_side * observation.premium_change_1m > 0.0
        )
        if confirmed:
            return replace(
                updated,
                decision=ForcedDecision.CONFIRMED,
                reason="POST_EXHAUSTION_REPRICE_TOWARD_PRE_SHOCK_VALUE_CONFIRMED",
            )

    if observation.bar_index >= state.expires_index:
        return replace(
            updated,
            decision=ForcedDecision.EXPIRED,
            reason="FORCED_FLOW_DID_NOT_COMPLETE_EXHAUSTION_AND_REPRICE_SEQUENCE",
        )
    return updated


__all__ = [
    "ForcedDecision",
    "ForcedEpisode",
    "ForcedObservation",
    "ForcedResponseThresholds",
    "ForcedShockEvidence",
    "ForcedShockThresholds",
    "advance_forced_episode",
    "classify_forced_shock",
]
