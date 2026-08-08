"""Persistent initiative ownership and causal shadow-reversal probes.

A stopped reversal establishes initiative in the opposite direction. A mere
completed close back through the swept source invalidates *outside acceptance*
but does not, by itself, prove that initiative transferred. The next reversal
setup against the active initiative is therefore observed as a shadow probe
rather than immediately risked:

* declared target delivered first -> the counter-reversal proved itself and the
  old initiative state is released;
* declared structural stop accepted first -> the old initiative is reconfirmed
  and the probe can route an opposite continuation without paying the failed
  fade loss;
* neither terminal boundary -> the market remains unresolved and no trade is
  manufactured.

The module owns causal state only. It never simulates orders, fills, cash, PnL,
or NAV.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from math import isfinite

from initiative_auction import InitiativeAuction
from model import Direction, TradePlan


class ProbeOutcome(str, Enum):
    WAITING = "WAITING"
    TARGET_DELIVERED = "TARGET_DELIVERED"
    STOP_ACCEPTED = "STOP_ACCEPTED"


@dataclass(frozen=True, slots=True)
class ProbeObservation:
    outcome: ProbeOutcome
    close: float
    bars_seen: int
    reason_code: str


@dataclass(slots=True)
class ShadowReversalProbe:
    """Observe one fully declared reversal thesis without risking capital."""

    plan: TradePlan
    owner_scenario_id: str
    armed_at_ns: int
    bars_seen: int = 0

    def __post_init__(self) -> None:
        if self.armed_at_ns < self.plan.observed_time_ns:
            raise ValueError("probe cannot arm before the source plan is observed")
        if not self.owner_scenario_id:
            raise ValueError("probe owner_scenario_id must not be empty")
        if self.plan.direction is Direction.LONG:
            if not self.plan.stop_price < self.plan.entry_reference < self.plan.target_price:
                raise ValueError("LONG probe geometry is inconsistent")
        else:
            if not self.plan.target_price < self.plan.entry_reference < self.plan.stop_price:
                raise ValueError("SHORT probe geometry is inconsistent")

    @property
    def continuation_direction(self) -> Direction:
        return (
            Direction.LONG
            if self.plan.direction is Direction.SHORT
            else Direction.SHORT
        )

    def observe_close(self, close: float) -> ProbeObservation:
        if close <= 0.0 or not isfinite(close):
            raise ValueError("probe close must be finite and positive")
        self.bars_seen += 1
        if self.plan.direction is Direction.LONG:
            target_delivered = close >= self.plan.target_price
            stop_accepted = close <= self.plan.stop_price
        else:
            target_delivered = close <= self.plan.target_price
            stop_accepted = close >= self.plan.stop_price
        if target_delivered:
            return ProbeObservation(
                ProbeOutcome.TARGET_DELIVERED,
                close,
                self.bars_seen,
                "SHADOW_REVERSAL_TARGET_DELIVERED",
            )
        if stop_accepted:
            return ProbeObservation(
                ProbeOutcome.STOP_ACCEPTED,
                close,
                self.bars_seen,
                "SHADOW_REVERSAL_STOP_ACCEPTED",
            )
        return ProbeObservation(
            ProbeOutcome.WAITING,
            close,
            self.bars_seen,
            "SHADOW_REVERSAL_UNRESOLVED",
        )

    def new_source_is_more_extreme(self, candidate: TradePlan) -> bool:
        if candidate.direction is not self.plan.direction:
            raise ValueError("only same-direction probes can be compared")
        if candidate.direction is Direction.LONG:
            return candidate.liquidity_level < self.plan.liquidity_level
        return candidate.liquidity_level > self.plan.liquidity_level


class PersistentInitiativeAuctionGate:
    """Own one global initiative epoch until market evidence transfers it."""

    def __init__(self) -> None:
        self._state: InitiativeAuction | None = None
        self._source_reclaim_notice: InitiativeAuction | None = None
        self._source_reclaimed = False
        # The existing initiative strategy asks ``state`` only to perform its
        # default hard invalidation. Probe routing temporarily defers that check
        # and inspects ``actual_state`` after the base strategy has completed.
        self.defer_blocking = False

    @staticmethod
    def opposite(direction: Direction) -> Direction:
        return Direction.SHORT if direction is Direction.LONG else Direction.LONG

    def accept_failed_reversal(
        self,
        *,
        blocked_reversal_direction: Direction,
        source_scenario_id: str,
        accepted_source_level: float,
        accepted_at_ns: int,
    ) -> InitiativeAuction:
        state = InitiativeAuction(
            blocked_reversal_direction=blocked_reversal_direction,
            initiative_direction=self.opposite(blocked_reversal_direction),
            accepted_source_level=accepted_source_level,
            source_scenario_id=source_scenario_id,
            accepted_at_ns=accepted_at_ns,
        )
        self._state = state
        self._source_reclaimed = False
        self._source_reclaim_notice = None
        return state

    def transfer_on_failed_reversal(
        self,
        *,
        blocked_reversal_direction: Direction,
        source_scenario_id: str,
        accepted_source_level: float,
        accepted_at_ns: int,
    ) -> tuple[InitiativeAuction, InitiativeAuction | None]:
        displaced = self._state
        state = self.accept_failed_reversal(
            blocked_reversal_direction=blocked_reversal_direction,
            source_scenario_id=source_scenario_id,
            accepted_source_level=accepted_source_level,
            accepted_at_ns=accepted_at_ns,
        )
        return state, displaced

    def actual_state(
        self,
        reversal_direction: Direction | None = None,
    ) -> InitiativeAuction | None:
        state = self._state
        if state is None:
            return None
        if (
            reversal_direction is not None
            and state.blocked_reversal_direction is not reversal_direction
        ):
            return None
        return state

    def state(self, reversal_direction: Direction) -> InitiativeAuction | None:
        if self.defer_blocking:
            return None
        return self.actual_state(reversal_direction)

    def is_blocked(self, reversal_direction: Direction) -> bool:
        return self.actual_state(reversal_direction) is not None

    @property
    def source_reclaimed(self) -> bool:
        return self._source_reclaimed

    def observe_close(self, close: float) -> tuple[InitiativeAuction, ...]:
        if close <= 0.0 or not isfinite(close):
            raise ValueError("close must be finite and positive")
        state = self._state
        if (
            state is not None
            and not self._source_reclaimed
            and state.acceptance_reclaimed(close)
        ):
            self._source_reclaimed = True
            self._source_reclaim_notice = state
        # Reclaim is diagnostic evidence, not sufficient transfer evidence.
        return tuple()

    def consume_source_reclaim_notice(self) -> InitiativeAuction | None:
        notice = self._source_reclaim_notice
        self._source_reclaim_notice = None
        return notice

    def observe_counter_acceptance(
        self,
        continuation_direction: Direction,
    ) -> InitiativeAuction | None:
        state = self._state
        if (
            state is None
            or state.blocked_reversal_direction is not continuation_direction
        ):
            return None
        self._state = None
        self._source_reclaimed = False
        self._source_reclaim_notice = None
        return state

    def clear(self, reversal_direction: Direction) -> InitiativeAuction | None:
        state = self.actual_state(reversal_direction)
        if state is None:
            return None
        self._state = None
        self._source_reclaimed = False
        self._source_reclaim_notice = None
        return state
