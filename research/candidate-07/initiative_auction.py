"""Causal initiative-auction ownership after failed absorption.

A stopped absorption/reclaim is observable acceptance beyond the swept boundary
and transfers control to the opposite initiative auction. Later local pools in
the failed reversal direction belong to that accepted leg until one of three
observable transitions occurs:

* the failed reversal's declared opposing liquidity is delivered;
* a confirmed acceptance-continuation plan proves initiative changed sides; or
* an absorption/reclaim in the opposite reversal direction itself stops,
  directly confirming opposite-side initiative acceptance.

There is no elapsed-time expiry, loss-count rule, model score, or PnL condition.
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
    opposing_delivery_price: float
    source_scenario_id: str
    accepted_source_level: float
    accepted_at_ns: int

    def __post_init__(self) -> None:
        if self.initiative_direction is self.blocked_reversal_direction:
            raise ValueError("initiative must oppose the blocked reversal")
        for name in ("opposing_delivery_price", "accepted_source_level"):
            value = float(getattr(self, name))
            if value <= 0.0 or not isfinite(value):
                raise ValueError(f"{name} must be finite and positive")
        if self.accepted_at_ns < 0:
            raise ValueError("accepted_at_ns must be non-negative")
        if not self.source_scenario_id:
            raise ValueError("source_scenario_id must not be empty")

    def delivery_reached(self, close: float) -> bool:
        if close <= 0.0 or not isfinite(close):
            raise ValueError("close must be finite and positive")
        if self.blocked_reversal_direction is Direction.LONG:
            return close >= self.opposing_delivery_price
        return close <= self.opposing_delivery_price


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
        opposing_delivery_price: float,
        source_scenario_id: str,
        accepted_source_level: float,
        accepted_at_ns: int,
    ) -> InitiativeAuction:
        state = InitiativeAuction(
            blocked_reversal_direction=blocked_reversal_direction,
            initiative_direction=self.opposite(blocked_reversal_direction),
            opposing_delivery_price=opposing_delivery_price,
            source_scenario_id=source_scenario_id,
            accepted_source_level=accepted_source_level,
            accepted_at_ns=accepted_at_ns,
        )
        self._states[blocked_reversal_direction] = state
        return state

    def transfer_on_failed_reversal(
        self,
        *,
        blocked_reversal_direction: Direction,
        opposing_delivery_price: float,
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
            opposing_delivery_price=opposing_delivery_price,
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
            if state.delivery_reached(close):
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
