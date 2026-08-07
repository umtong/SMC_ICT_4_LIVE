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
        """Return an objective ledger namespace independent of the parent bias."""

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
                previous_state="IDLE",
                next_state="OBJECTIVE_ACTIVE",
                reason="PREEXISTING_UNRESOLVED_OBJECTIVE_BOUND",
                reference_price=objective.level,
                details=self._objective_details(objective),
            ),
        )

    @staticmethod
    def _objective_details(objective: _BoundObjective) -> dict[str, Any]:
        return {
            "direction": objective.direction,
            "side": objective.side,
            "level": objective.level,
            "source_ts_ns": objective.source_ts_ns,
            "confirmed_ts_ns": objective.confirmed_ts_ns,
            "kind": objective.kind,
            "target_reason": objective.reason,
            "ladder_index": objective.ladder_index,
            "ladder_size": objective.ladder_size,
            "entry_armed": objective.entry_armed,
        }

    def _evaluate_completed_bias(
        self,
        bar: _AuctionBar,
        snapshot: PrimitiveSnapshot,
    ) -> tuple[ScenarioTransition, ...]:
        previous_context = self._bias.context_id if self._bias is not None else None
        transitions = list(super()._evaluate_completed_bias(bar, snapshot))
        if not self._lifecycle_enabled():
            return tuple(transitions)

        bias = self._bias
        if bias is None or bias.context_id == previous_context:
            return tuple(transitions)
        self._clear_objectives(previous_context)
        bound = self._bind_objective_ladder(bar, bias)
        transitions.extend(bound)
        if not self._objective_ladder:
            reset = self._reset_bias_and_sweep(
                snapshot,
                "NO_PREEXISTING_UNRESOLVED_OBJECTIVE",
            )
            transitions.extend(reset.transitions)
        return tuple(transitions)

    def _objective_crossed(
        self,
        objective: _BoundObjective,
        snapshot: PrimitiveSnapshot,
    ) -> bool:
        observation = snapshot.observation
        if objective.direction == "LONG":
            return observation.high >= objective.level
        return observation.low <= objective.level

    def _advance_consumed_objectives(
        self,
        snapshot: PrimitiveSnapshot,
    ) -> ScenarioStep:
        transitions: list[ScenarioTransition] = []
        bias = self._bias
        if bias is None:
            self._clear_objectives()
            return ScenarioStep()

        objective = self._current_objective()
        while objective is not None and self._objective_crossed(objective, snapshot):
            transitions.append(
                self._objective_transition(
                    scenario_id=self._objective_scenario_id(bias.context_id),
                    previous_state=("OBJECTIVE_ENTRY_ARMED" if objective.entry_armed else "OBJECTIVE_ACTIVE"),
                    next_state="OBJECTIVE_CONSUMED",
                    reason="BOUND_OBJECTIVE_CONSUMED",
                    reference_price=objective.level,
                    details=self._objective_details(objective),
                ),
            )
            if self._sweep is not None:
                transitions.append(
                    self._sweep_transition(
                        self._sweep,
                        self._sweep.state,
                        "RESET",
                        "BOUND_OBJECTIVE_CONSUMED_BEFORE_RESPONSE_COMPLETED",
                        objective.level,
                        self._objective_details(objective),
                    ),
                )
                self._sweep = None
            self._objective_index += 1
            objective = self._current_objective()
            if objective is not None:
                transitions.append(
                    self._objective_transition(
                        scenario_id=self._objective_scenario_id(bias.context_id),
                        previous_state="OBJECTIVE_CONSUMED",
                        next_state="OBJECTIVE_ACTIVE",
                        reason="NEXT_PREEXISTING_OBJECTIVE_ACTIVATED",
                        reference_price=objective.level,
                        details=self._objective_details(objective),
                    ),
                )

        if objective is None and self._objective_ladder:
            reset = self._reset_bias_and_sweep(snapshot, "ALL_BOUND_OBJECTIVES_CONSUMED")
            transitions.extend(reset.transitions)
        return ScenarioStep(transitions=tuple(transitions))

    def _advance_bias(self, snapshot: PrimitiveSnapshot) -> ScenarioStep:
        step = super()._advance_bias(snapshot)
        if not self._lifecycle_enabled() or self._bias is None:
            return step

        transitions = list(step.transitions)
        consumed = self._advance_consumed_objectives(snapshot)
        transitions.extend(consumed.transitions)
        bias = self._bias
        if bias is None:
            return ScenarioStep(transitions=tuple(transitions))

        if self._origin_invalidation_enabled() and snapshot.index > bias.created_index:
            close = snapshot.observation.close
            origin_lost = (
                close <= bias.origin
                if bias.direction == "LONG"
                else close >= bias.origin
            )
            if origin_lost:
                objective = self._current_objective()
                objective_previous_state = (
                    "OBJECTIVE_ENTRY_ARMED"
                    if objective is not None and objective.entry_armed
                    else "OBJECTIVE_ACTIVE"
                )
                transitions.append(
                    self._objective_transition(
                        scenario_id=self._objective_scenario_id(bias.context_id),
                        previous_state=objective_previous_state,
                        next_state="RESET",
                        reason="UOAM_BOUND_IMPULSE_ORIGIN_REBALANCED",
                        reference_price=bias.origin,
                        details={
                            "direction": bias.direction,
                            "origin": bias.origin,
                            "close": close,
                            "current_objective": (
                                None
                                if self._current_objective() is None
                                else self._objective_details(self._current_objective())
                            ),
                        },
                    ),
                )
                reset = self._reset_bias_and_sweep(
                    snapshot,
                    "UOAM_BOUND_IMPULSE_ORIGIN_REBALANCED",
                )
                transitions.extend(reset.transitions)
        return ScenarioStep(transitions=tuple(transitions))

    def _reset_bias_and_sweep(self, snapshot: PrimitiveSnapshot, reason: str) -> ScenarioStep:
        context_id = self._bias.context_id if self._bias is not None else None
        step = super()._reset_bias_and_sweep(snapshot, reason)
        self._clear_objectives(context_id)
        return step

    def _maybe_start_sweep(self, snapshot: PrimitiveSnapshot) -> ScenarioTransition | None:
        if self._lifecycle_enabled():
            objective = self._current_objective()
            if objective is None or objective.entry_armed:
                return None
        return super()._maybe_start_sweep(snapshot)

    def _nearest_target_objective(self, direction: str, entry: float) -> tuple[float, str] | None:
        if not self._lifecycle_enabled():
            return super()._nearest_target_objective(direction, entry)
        objective = self._current_objective()
        if objective is None or objective.direction != direction:
            return None
        if direction == "LONG" and objective.level <= entry:
            return None
        if direction == "SHORT" and objective.level >= entry:
            return None
        return objective.level, objective.reason

    def _select_target(
        self,
        direction: str,
        entry: float,
        stop: float,
        candidates: list[tuple[float | None, str]],
    ) -> tuple[float, str] | None:
        if not self._lifecycle_enabled():
            return super()._select_target(direction, entry, stop, candidates)
        objective = self._nearest_target_objective(direction, entry)
        if objective is None:
            return None
        risk = abs(entry - stop)
        reward = objective[0] - entry if direction == "LONG" else entry - objective[0]
        minimum_rr = float(self.params.get("minimum_structural_rr", 0.75))
        if risk <= 0.0 or reward <= 0.0 or reward / risk < minimum_rr:
            return None
        return objective

    def _emit(self, snapshot: PrimitiveSnapshot, bias: _Bias, sweep: _SweepEpisode) -> ScenarioStep:
        objective = self._current_objective()
        step = super()._emit(snapshot, bias, sweep)
        if not self._lifecycle_enabled() or step.signal is None or objective is None:
            return step

        objective.entry_armed = True
        exit_codes = [
            "UOAM_BOUND_IMPULSE_ORIGIN_REBALANCED",
            "BULLISH_ACCEPTED_BOUNDARY_LOST",
            "BEARISH_ACCEPTED_BOUNDARY_LOST",
            "HIGHER_TIMEFRAME_BIAS_REPLACED",
            "BOUND_OBJECTIVE_CONSUMED",
            "ALL_BOUND_OBJECTIVES_CONSUMED",
        ]
        details = {
            **dict(step.signal.details),
            "bound_objective": self._objective_details(objective),
            "objective_lifecycle_contract": {
                "prior_confirmation_required": True,
                "confirmation_precedes_accepting_auction": True,
                "untouched_by_accepting_impulse": True,
                "one_entry_per_objective": True,
                "objective_ladder": True,
                "origin_invalidation": self._origin_invalidation_enabled(),
            },
            "causal_exit_reason_codes": exit_codes,
            "causal_exit_open_position": self._position_exit_enabled(),
        }
        signal: ScenarioSignal = replace(step.signal, family="UOAM", details=details)
        transition = self._objective_transition(
            scenario_id=self._objective_scenario_id(bias.context_id),
            previous_state="OBJECTIVE_ACTIVE",
            next_state="OBJECTIVE_ENTRY_ARMED",
            reason="BOUND_OBJECTIVE_ENTRY_ARMED",
            reference_price=objective.level,
            details=self._objective_details(objective),
        )
        return ScenarioStep(transitions=tuple((*step.transitions, transition)), signal=signal)

    def abort_active(self, snapshot: PrimitiveSnapshot, reason: str) -> ScenarioStep:
        step = super().abort_active(snapshot, reason)
        self._clear_objectives()
        return step
