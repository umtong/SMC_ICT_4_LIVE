"""Causal failed-absorption memory for candidate-07.

A stopped absorption trade is evidence that one specific swept liquidity source
was accepted rather than absorbed. It does not invalidate every later reversal
in the same direction. The gate therefore owns state by direction *and the
failed source-liquidity level*. Repeated attempts against that same source remain
blocked until the originally expected opposing liquidity is delivered, while an
independent source in the same direction remains eligible.

This module records causal state only. It does not calculate orders, fills, PnL,
cash, NAV, or elapsed-time cooldowns.
"""
from __future__ import annotations

from dataclasses import dataclass
from math import isfinite

from model import Direction


@dataclass(frozen=True, slots=True)
class CascadeBlock:
    direction: Direction
    reset_price: float
    source_scenario_id: str
    blocked_at_ns: int
    source_key: str
    source_liquidity_level: float | None = None

    def __post_init__(self) -> None:
        if self.reset_price <= 0.0 or not isfinite(self.reset_price):
            raise ValueError("reset_price must be finite and positive")
        if self.blocked_at_ns < 0:
            raise ValueError("blocked_at_ns must be non-negative")
        if not self.source_scenario_id:
            raise ValueError("source_scenario_id must not be empty")
        if not self.source_key:
            raise ValueError("source_key must not be empty")
        if self.source_liquidity_level is not None and (
            self.source_liquidity_level <= 0.0
            or not isfinite(self.source_liquidity_level)
        ):
            raise ValueError(
                "source_liquidity_level must be finite and positive"
            )

    def reset_reached(self, close: float) -> bool:
        if close <= 0.0 or not isfinite(close):
            raise ValueError("close must be finite and positive")
        if self.direction is Direction.LONG:
            return close >= self.reset_price
        return close <= self.reset_price


class FailedAbsorptionGate:
    """Block only a repeated reversal against the same failed source.

    The source owner is the exact causal liquidity level already present in the
    ``TradePlan``. ``float.hex`` is used as a stable in-process identity for the
    same loaded market price. The legacy no-source API remains available for old
    unit callers, but production strategy calls always provide the source level.
    """

    _LEGACY_SOURCE = "LEGACY_DIRECTION_OWNER"

    def __init__(self) -> None:
        self._blocks: dict[tuple[Direction, str], CascadeBlock] = {}

    @classmethod
    def source_key(
        cls,
        direction: Direction,
        source_liquidity_level: float | None,
    ) -> str:
        if source_liquidity_level is None:
            return cls._LEGACY_SOURCE
        level = float(source_liquidity_level)
        if level <= 0.0 or not isfinite(level):
            raise ValueError(
                "source_liquidity_level must be finite and positive"
            )
        return f"{direction.value}:{level.hex()}"

    def block(
        self,
        *,
        direction: Direction,
        reset_price: float,
        source_scenario_id: str,
        blocked_at_ns: int,
        source_liquidity_level: float | None = None,
    ) -> CascadeBlock:
        key = self.source_key(direction, source_liquidity_level)
        state = CascadeBlock(
            direction=direction,
            reset_price=reset_price,
            source_scenario_id=source_scenario_id,
            blocked_at_ns=blocked_at_ns,
            source_key=key,
            source_liquidity_level=source_liquidity_level,
        )
        self._blocks[(direction, key)] = state
        return state

    def state(
        self,
        direction: Direction,
        source_liquidity_level: float | None = None,
    ) -> CascadeBlock | None:
        if source_liquidity_level is not None:
            key = self.source_key(direction, source_liquidity_level)
            return self._blocks.get((direction, key))
        matches = [
            state
            for (owner_direction, _), state in self._blocks.items()
            if owner_direction is direction
        ]
        if not matches:
            return None
        return max(matches, key=lambda state: state.blocked_at_ns)

    def is_blocked(
        self,
        direction: Direction,
        source_liquidity_level: float | None = None,
    ) -> bool:
        if source_liquidity_level is not None:
            return self.state(direction, source_liquidity_level) is not None
        return any(
            owner_direction is direction
            for owner_direction, _ in self._blocks
        )

    def observe_close(self, close: float) -> tuple[CascadeBlock, ...]:
        released: list[CascadeBlock] = []
        for owner, state in tuple(self._blocks.items()):
            if state.reset_reached(close):
                released.append(state)
                del self._blocks[owner]
        released.sort(key=lambda state: (state.blocked_at_ns, state.source_key))
        return tuple(released)

    def clear(
        self,
        direction: Direction,
        source_liquidity_level: float | None = None,
    ) -> CascadeBlock | None:
        if source_liquidity_level is not None:
            key = self.source_key(direction, source_liquidity_level)
            return self._blocks.pop((direction, key), None)
        state = self.state(direction)
        if state is None:
            return None
        return self._blocks.pop((direction, state.source_key), None)
