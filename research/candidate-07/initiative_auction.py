"""Causal initiative-auction ownership after failed absorption.

A stopped absorption/reclaim is observable acceptance beyond the swept source
boundary and transfers control to the opposite initiative auction. Later local
pools in the failed reversal direction belong to that accepted leg only while
the accepted boundary remains outside value. The state changes through market
structure, never through elapsed time, loss count, PnL, or a fitted score:

* a completed close reclaims the accepted source boundary, invalidating the
  outside acceptance;
* a confirmed acceptance-continuation plan proves initiative changed sides; or
* an absorption/reclaim in the opposite reversal direction itself stops,
  directly confirming opposite-side initiative acceptance.

The failed reversal's original opposing-liquidity target is deliberately not an
initiative lifetime condition. That target belongs to the failed trade thesis;
reusing it after the thesis fails reverses the meaning of the accepted auction.
This module owns causal state only and never computes orders, fills, PnL, cash,
or NAV.
"""
from __future__ import annotations

from dataclasses import dataclass
from math import isfinite

from model import Direction


@dataclass(frozen=True, slots=True)
class InitiativeAuction:
    blocked_reversal_direction: Direction
    initiative_direction: Direction
    accepted_source_level: float
    source_scenario_id: str
    accepted_at_ns: int

    def __post_init__(self) -> None:
        if self.initiative_direction is self.blocked_reversal_direction:
            raise ValueError("initiative must oppose the blocked reversal")
        if self.accepted_source_level <= 0.0 or not isfinite(
            self.accepted_source_level,
        ):
            raise ValueError("accepted_source_level must be finite and positive")
        if self.accepted_at_ns < 0:
            raise ValueError("accepted_at_ns must be non-negative")
        if not self.source_scenario_id:
            raise ValueError("source_scenario_id must not be empty")

    def acceptance_reclaimed(self, close: float) -> bool:
        """Return whether a completed close invalidates outside acceptance.

        A failed SHORT reversal confirms bullish acceptance above an upper
        source, so the state remains active only while closes stay above that
        source. A failed LONG reversal is symmetric below a lower source.
        """
        if close <= 0.0 or not isfinite(close):
            raise ValueError("close must be finite and positive")
        if self.blocked_reversal_direction is Direction.LONG:
            return close >= self.accepted_source_level
        return close <= self.accepted_source_level


class InitiativeAuctionGate:
    """Own one currently accepted auction state for each reversal direction."""

    def __init__(self) -> None:
        self._states: dict[Direction, InitiativeAuction] = {}

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
        self._states[blocked_reversal_direction] = state
        return state

    def transfer_on_failed_reversal(
        self,
        *,
        blocked_reversal_direction: Direction,
        source_scenario_id: str,
        accepted_source_level: float,
        accepted_at_ns: int,
    ) -> tuple[InitiativeAuction, InitiativeAuction | None]:
        """Install new initiative and displace its causal opposite, if active."""
        displaced = self._states.pop(
            self.opposite(blocked_reversal_direction),
            None,
        )
        state = self.accept_failed_reversal(
            blocked_reversal_direction=blocked_reversal_direction,
            source_scenario_id=source_scenario_id,
            accepted_source_level=accepted_source_level,
            accepted_at_ns=accepted_at_ns,
        )
        return state, displaced

    def state(self, reversal_direction: Direction) -> InitiativeAuction | None:
        return self._states.get(reversal_direction)

    def is_blocked(self, reversal_direction: Direction) -> bool:
        return reversal_direction in self._states

    def observe_close(self, close: float) -> tuple[InitiativeAuction, ...]:
        released: list[InitiativeAuction] = []
        for direction, state in tuple(self._states.items()):
            if state.acceptance_reclaimed(close):
                released.append(state)
                del self._states[direction]
        released.sort(key=lambda state: state.accepted_at_ns)
        return tuple(released)

    def observe_counter_acceptance(
        self,
        continuation_direction: Direction,
    ) -> InitiativeAuction | None:
        """Release when a confirmed continuation proves the blocked direction."""
        return self._states.pop(continuation_direction, None)

    def clear(self, reversal_direction: Direction) -> InitiativeAuction | None:
        return self._states.pop(reversal_direction, None)
