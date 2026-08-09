"""Pure causal effort/result auction router for Candidate 16.

The router deliberately contains no orders, fills, accounting, or PnL logic.
One parent boundary interaction can terminate exactly once as FAILED_AUCTION,
ACCEPTANCE_CONTINUATION, or UNRESOLVED. Observations must be supplied in
strictly increasing completed-bar order.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from enum import StrEnum
import math


class AuctionDecision(StrEnum):
    PENDING = "PENDING"
    FAILED_AUCTION = "FAILED_AUCTION"
    ACCEPTANCE_CONTINUATION = "ACCEPTANCE_CONTINUATION"
    UNRESOLVED = "UNRESOLVED"


@dataclass(frozen=True, slots=True)
class RouterThresholds:
    max_observation_bars: int = 3
    min_directional_effort: float = 0.14
    failed_max_progress_atr: float = 0.24
    failed_reclaim_atr: float = 0.01
    failed_max_efficiency: float = 0.42
    acceptance_min_outside_closes: int = 2
    acceptance_min_progress_atr: float = 0.24
    acceptance_min_efficiency: float = 0.34
    acceptance_min_close_location: float = 0.56
    acceptance_max_adverse_reentry_atr: float = 0.06

    def __post_init__(self) -> None:
        if self.max_observation_bars < 2:
            raise ValueError("max_observation_bars must be at least 2")
        if self.acceptance_min_outside_closes < 2:
            raise ValueError("acceptance requires at least two completed outside closes")
        if self.acceptance_min_outside_closes > self.max_observation_bars:
            raise ValueError("outside-close requirement exceeds observation window")
        if self.min_directional_effort < 0.0:
            raise ValueError("min_directional_effort must be non-negative")


@dataclass(frozen=True, slots=True)
class AuctionObservation:
    bar_index: int
    ts_event: int
    open: float
    high: float
    low: float
    close: float
    flow_60s: float
    notional_burst: float
    efficiency_60s: float
    same_side_depth_change_1m: float

    def __post_init__(self) -> None:
        if self.bar_index < 0 or self.ts_event < 0:
            raise ValueError("bar_index and ts_event must be non-negative")
        if not (self.low <= min(self.open, self.close) <= max(self.open, self.close) <= self.high):
            raise ValueError("OHLC values are inconsistent")


@dataclass(frozen=True, slots=True)
class ParentAuction:
    scenario_id: str
    direction: int
    pool_level: float
    atr: float
    started_index: int
    last_index: int
    observations: int = 0
    outside_closes: int = 0
    peak_directional_effort: float = 0.0
    peak_progress_atr: float = 0.0
    latest_progress_atr: float = 0.0
    latest_efficiency: float = 0.0
    latest_close_location: float = 0.0
    latest_depth_response: float = 0.0
    decision: AuctionDecision = AuctionDecision.PENDING
    reason: str = "INTERACTION_OBSERVED"

    def __post_init__(self) -> None:
        if self.direction not in (-1, 1):
            raise ValueError("direction must be -1 or +1")
        if self.atr <= 0.0 or not math.isfinite(self.atr):
            raise ValueError("atr must be finite and positive")
        if self.decision is not AuctionDecision.PENDING and self.observations == 0:
            raise ValueError("a terminal decision requires at least one observation")


def _finite(value: float, default: float = 0.0) -> float:
    return value if math.isfinite(value) else default


def _directional_close_location(observation: AuctionObservation, direction: int) -> float:
    span = max(observation.high - observation.low, 1e-12)
    if direction > 0:
        return (observation.close - observation.low) / span
    return (observation.high - observation.close) / span


def _directional_effort(observation: AuctionObservation, direction: int) -> float:
    flow = max(0.0, direction * _finite(observation.flow_60s))
    burst = max(1.0, _finite(observation.notional_burst, 1.0))
    return flow * burst


def observe(
    state: ParentAuction,
    observation: AuctionObservation,
    thresholds: RouterThresholds,
) -> ParentAuction:
    """Advance one parent auction with one newly completed causal observation."""
    if state.decision is not AuctionDecision.PENDING:
        raise ValueError("terminal parent auction cannot be advanced")
    if observation.bar_index <= state.last_index:
        raise ValueError("observations must have strictly increasing bar_index")

    progress_atr = state.direction * (observation.close - state.pool_level) / state.atr
    effort = _directional_effort(observation, state.direction)
    efficiency = max(0.0, _finite(observation.efficiency_60s))
    close_location = _directional_close_location(observation, state.direction)
    outside = progress_atr > 0.0
    observations = state.observations + 1
    outside_closes = state.outside_closes + int(outside)
    peak_progress = max(state.peak_progress_atr, progress_atr)
    peak_effort = max(state.peak_directional_effort, effort)

    updated = replace(
        state,
        last_index=observation.bar_index,
        observations=observations,
        outside_closes=outside_closes,
        peak_directional_effort=peak_effort,
        peak_progress_atr=peak_progress,
        latest_progress_atr=progress_atr,
        latest_efficiency=efficiency,
        latest_close_location=close_location,
        latest_depth_response=_finite(observation.same_side_depth_change_1m),
    )

    failed = (
        peak_effort >= thresholds.min_directional_effort
        and peak_progress <= thresholds.failed_max_progress_atr
        and progress_atr <= -thresholds.failed_reclaim_atr
        and efficiency <= thresholds.failed_max_efficiency
    )
    if failed:
        return replace(
            updated,
            decision=AuctionDecision.FAILED_AUCTION,
            reason="HIGH_EFFORT_LOW_PROGRESS_FAST_BOUNDARY_RECLAIM",
        )

    accepted = (
        outside_closes >= thresholds.acceptance_min_outside_closes
        and progress_atr >= thresholds.acceptance_min_progress_atr
        and effort >= thresholds.min_directional_effort
        and efficiency >= thresholds.acceptance_min_efficiency
        and close_location >= thresholds.acceptance_min_close_location
    )
    if accepted:
        return replace(
            updated,
            decision=AuctionDecision.ACCEPTANCE_CONTINUATION,
            reason="SUSTAINED_OUTSIDE_RESIDENCE_WITH_EFFICIENT_PROGRESS",
        )

    hard_reentry = progress_atr < -thresholds.acceptance_max_adverse_reentry_atr
    if observations >= thresholds.max_observation_bars or hard_reentry:
        return replace(
            updated,
            decision=AuctionDecision.UNRESOLVED,
            reason=(
                "ADVERSE_REENTRY_WITHOUT_FAILED_AUCTION_EFFORT"
                if hard_reentry
                else "OBSERVATION_WINDOW_EXPIRED_WITHOUT_COMPLETED_STATE"
            ),
        )
    return replace(updated, reason="STATE_NOT_YET_COMPLETED")


__all__ = [
    "AuctionDecision",
    "AuctionObservation",
    "ParentAuction",
    "RouterThresholds",
    "observe",
]
