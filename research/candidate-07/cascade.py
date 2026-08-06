"""Causal failed-absorption memory for candidate-07.

A stopped absorption trade is market evidence that price accepted beyond the
swept pool. Repeating the same reversal before the originally expected
opposing liquidity is delivered is the same invalidated thesis, not a new
independent opportunity. This module records that state only; it does not
calculate orders, fills, PnL, or portfolio values.
"""
from __future__ import annotations

from dataclasses import dataclass

from model import Direction


@dataclass(frozen=True, slots=True)
class CascadeBlock:
    direction: Direction
    reset_price: float
    source_scenario_id: str
    blocked_at_ns: int

    def __post_init__(self) -> None:
        if self.reset_price <= 0.0:
            raise ValueError("reset_price must be positive")
        if self.blocked_at_ns < 0:
            raise ValueError("blocked_at_ns must be non-negative")
        if not self.source_scenario_id:
            raise ValueError("source_scenario_id must not be empty")

    def reset_reached(self, close: float) -> bool:
        if close <= 0.0:
            raise ValueError("close must be positive")
        if self.direction is Direction.LONG:
            return close >= self.reset_price
        return close <= self.reset_price


class FailedAbsorptionGate:
    """Block repeated same-direction reversals until structural delivery.

    The gate is deliberately symmetric. A failed long absorption blocks only
    later long absorption plans; a failed short absorption blocks only later
    short plans. The state clears from market price, never from elapsed time,
    a risk score, or a manually chosen cooldown.
    """

    def __init__(self) -> None:
        self._blocks: dict[Direction, CascadeBlock] = {}

    def block(
        self,
        *,
        direction: Direction,
        reset_price: float,
        source_scenario_id: str,
        blocked_at_ns: int,
    ) -> CascadeBlock:
        state = CascadeBlock(
            direction=direction,
            reset_price=reset_price,
            source_scenario_id=source_scenario_id,
            blocked_at_ns=blocked_at_ns,
        )
        self._blocks[direction] = state
        return state

    def state(self, direction: Direction) -> CascadeBlock | None:
        return self._blocks.get(direction)

    def is_blocked(self, direction: Direction) -> bool:
        return direction in self._blocks

    def observe_close(self, close: float) -> tuple[CascadeBlock, ...]:
        released: list[CascadeBlock] = []
        for direction, state in tuple(self._blocks.items()):
            if state.reset_reached(close):
                released.append(state)
                del self._blocks[direction]
        return tuple(released)

    def clear(self, direction: Direction) -> CascadeBlock | None:
        return self._blocks.pop(direction, None)
