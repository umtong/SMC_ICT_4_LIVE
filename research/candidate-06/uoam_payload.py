"""Unresolved-objective lifecycle on top of the causal HML auction relay.

The detector keeps the existing 60-minute accepted-auction context, causally
confirmed five-minute swing/equal-liquidity pools, counter-bias sweep, and
separate one-minute response.  It changes the market thesis in one place:
the objective must have existed before the accepting impulse, remain untouched
by that impulse, and remain unresolved at entry.  Objectives are bound as an
ordered ladder to the context and are consumed at most once.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any, Mapping

from hierarchical_multi_liquidity_engine import HierarchicalMultiLiquidityEngine
from hierarchical_pool_engine import _LiquidityPool
from hierarchical_sweep_engine import _AuctionBar, _Bias, _SweepEpisode
from lrb_types import PrimitiveSnapshot, ScenarioSignal, ScenarioStep, ScenarioTransition


@dataclass(slots=True)
class _BoundObjective:
    direction: str
    side: str
    level: float
    source_ts_ns: int
    confirmed_ts_ns: int
    kind: str
    reason: str
    ladder_index: int
    ladder_size: int
    entry_armed: bool = False


class UnresolvedObjectiveLifecycleEngine(HierarchicalMultiLiquidityEngine):
    """Trade only toward a pre-existing, still-unresolved objective.

    The original HML engine selected the nearest opposite-side pool at signal
    time.  That can bind a target which formed after the higher-timeframe
    impulse, or keep using a structurally valid bias after its original target
    liquidity has already been consumed.  UOAM snapshots only pools confirmed
    before the completed accepting auction and lying beyond its extreme.  The
    ladder advances as objectives are consumed and the context ends when no
    pre-existing objective remains or the impulse origin is fully rebalanced.
    """

    def __init__(self, params: Mapping[str, Any]):
        super().__init__(params)
        self._objective_context_id: str | None = None
        self._objective_ladder: list[_BoundObjective] = []
        self._objective_index = 0

    def _lifecycle_enabled(self) -> bool:
        return bool(self.params.get("uoam_use_objective_lifecycle", True))

    def _origin_invalidation_enabled(self) -> bool:
        return bool(self.params.get("uoam_use_origin_invalidation", True))

    def _position_exit_enabled(self) -> bool:
        return bool(self.params.get("uoam_exit_open_position_on_invalidation", True))

    @staticmethod
    def _objective_reason(direction: str, kind: str) -> str:
        equal = kind in {"EQUAL_HIGH", "EQUAL_LOW"}
        if direction == "LONG":
            return (
                "BOUND_PREEXISTING_EQUAL_BUYSIDE_LIQUIDITY"
                if equal
                else "BOUND_PREEXISTING_SWING_BUYSIDE_LIQUIDITY"
            )
        return (
            "BOUND_PREEXISTING_EQUAL_SELLSIDE_LIQUIDITY"
            if equal
            else "BOUND_PREEXISTING_SWING_SELLSIDE_LIQUIDITY"
        )

    @staticmethod
    def _objective_scenario_id(context_id: str) -> str:
        """Return an objective-ledger namespace independent of the parent bias."""

        return f"{context_id}:UOAM-OBJECTIVE"

    @staticmethod
    def _objective_transition(
        *,
        scenario_id: str,
        previous_state: str,
        next_state: str,
        reason: str,
        reference_price: float | None,
        details: Mapping[str, Any],
    ) -> ScenarioTransition:
        return ScenarioTransition(
            scenario_id=scenario_id,
            event_type="UOAM_OBJECTIVE_TRANSITION",
            previous_state=previous_state,
            next_state=next_state,
            reason_code=reason,
            reference_price=reference_price,
            details=dict(details),
        )

    def _clear_objectives(self, context_id: str | None = None) -> None:
        if context_id is not None and context_id != self._objective_context_id:
            return
        self._objective_context_id = None
        self._objective_ladder = []
        self._objective_index = 0

    def _current_objective(self) -> _BoundObjective | None:
        if not self._lifecycle_enabled():
            return None
        if not 0 <= self._objective_index < len(self._objective_ladder):
            return None
        return self._objective_ladder[self._objective_index]

    def _eligible_objective_pools(
        self,
        bar: _AuctionBar,
        bias: _Bias,
    ) -> list[_LiquidityPool]:
        side = "UPPER" if bias.direction == "LONG" else "LOWER"
        pools = [
            pool
            for pool in self._liquidity_pools
            if pool.side == side
            and pool.confirmed_ts_ns < bar.start_ts_ns
            and (
                (pool.level > bar.high)
                if bias.direction == "LONG"
                else (pool.level < bar.low)
            )
        ]
        pools.sort(
            key=lambda pool: (
                pool.level - bar.close
                if bias.direction == "LONG"
                else bar.close - pool.level
            ),
        )
        # Equal and swing detectors can describe the same price.  Preserve the
        # earliest causal record while removing only numerically identical
        # objectives; no performance-dependent distance is introduced.
        unique: list[_LiquidityPool] = []
        epsilon = max(abs(bar.close) * 1e-9, 1e-12)
        for pool in pools:
            if any(abs(pool.level - prior.level) <= epsilon for prior in unique):
                continue
            unique.append(pool)
        return unique

    def _bind_objective_ladder(
        self,
        bar: _AuctionBar,
        bias: _Bias,
    ) -> tuple[ScenarioTransition, ...]:
        pools = self._eligible_objective_pools(bar, bias)
        if not pools:
            self._clear_objectives()
            return (
                self._objective_transition(
                    scenario_id=self._objective_scenario_id(bias.context_id),
                    previous_state="IDLE",
                    next_state="RESET",
                    reason="NO_PREEXISTING_UNRESOLVED_OBJECTIVE",
                    reference_price=bar.close,
                    details={
                        "direction": bias.direction,
                        "acceptance_start_ts_ns": bar.start_ts_ns,
                        "acceptance_end_ts_ns": bar.end_ts_ns,
                        "acceptance_high": bar.high,
                        "acceptance_low": bar.low,
                    },
                ),
            )

        ladder: list[_BoundObjective] = []
        size = len(pools)
        for index, pool in enumerate(pools):
            key = (pool.side, pool.source_ts_ns)
            kind = self._pool_kinds.get(key, "CONFIRMED_SWING")
            ladder.append(
                _BoundObjective(
                    direction=bias.direction,
                    side=pool.side,
                    level=pool.level,
                    source_ts_ns=pool.source_ts_ns,
                    confirmed_ts_ns=pool.confirmed_ts_ns,
                    kind=kind,
                    reason=self._objective_reason(bias.direction, kind),
                    ladder_index=index,
                    ladder_size=size,
                ),
            )
        self._objective_context_id = bias.context_id
        self._objective_ladder = ladder
        self._objective_index = 0
        objective = ladder[0]
        return (
            self._objective_transition(
                scenario_id=self._objective_scenario_id(bias.context_id),
                previous_state="IDLE", next_state="OBJECTIVE_ACTIVE", reason="PREEXISTING_UNRESOLVED_OBJECTIVE_BOUND", reference_price=objective.level, details=self._objective_details(objective)),
        )
