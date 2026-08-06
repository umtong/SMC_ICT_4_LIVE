"""Objective-Lifecycle Acceptance Relay (OLAR) causal state machine.

OLAR preserves the leading candidate-06 HML pattern detector -- completed HTF
acceptance, causally confirmed 5-minute swing/equal liquidity, a counter-bias
sweep, and a separate one-minute response -- but replaces its open-ended context
and target reuse with explicit market-state lifecycles:

* every confirmed price objective is available only until touched, reserved, or
  invalidated;
* each completed 15-minute same-direction acceptance creates one delivery leg
  and at most one entry signal;
* a completed opposing 15-minute acceptance invalidates the HTF context;
* a completed opposing control bar through the active leg origin suspends the
  leg and requests a structural position exit;
* targets are unresolved confirmed pools or the current leg extreme only. No
  dynamic fast/slow rolling extrema or synthetic ATR extension is admitted.

The module does not construct orders, size positions, calculate PnL, or emulate
fills. Those contracts remain in the existing NautilusTrader path.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from statistics import median
from typing import Any, Mapping

from causal_clock import source_bar_datetime
from hierarchical_multi_liquidity_engine import HierarchicalMultiLiquidityEngine
from hierarchical_pool_engine import _LiquidityPool
from hierarchical_sweep_engine import _AuctionBar, _Bias, _SweepEpisode
from lrb_types import PrimitiveSnapshot, ScenarioSignal, ScenarioStep, ScenarioTransition
from objective_lifecycle_core import (
    ControlAuction,
    ControlDecision,
    ControlThresholds,
    DirectionalLeg,
    ObjectiveKey,
    ObjectiveLedger,
    ObjectiveState,
    classify_control_auction,
)


@dataclass(frozen=True, slots=True)
class _ObjectiveChoice:
    key: ObjectiveKey
    level: float
    reason: str
    details: Mapping[str, Any]


class ObjectiveLifecycleAcceptanceRelayEngine(HierarchicalMultiLiquidityEngine):
    """HML continuation with event-based context, leg, and objective lifecycles."""

    def __init__(self, params: Mapping[str, Any]):
        super().__init__(params)
        self._control_period = int(self.params.get("olar_control_period_minutes", 15))
        if self._control_period < 5 or 1440 % self._control_period != 0:
            raise ValueError("olar control period must be at least 5 and divide one UTC day")
        if self._control_period >= self._bias_period:
            raise ValueError("olar control period must be smaller than the HTF bias period")
        if self._bias_period % self._control_period != 0:
            raise ValueError("olar control period must divide the HTF bias period")

        self._objective_ledger = ObjectiveLedger()
        self._known_pool_objectives: set[ObjectiveKey] = set()
        self._active_leg: DirectionalLeg | None = None
        self._leg_sequence = 0
        self._pending_position_exits: list[dict[str, Any]] = []
        self._one_use_objectives = bool(self.params.get("olar_use_objective_one_use", True))

        self._control_current: dict[str, Any] | None = None
        self._control_history: list[ControlAuction] = []
        self._control_true_ranges: list[float] = []
        self._control_volumes: list[float] = []
        self._last_control_decision: ControlDecision | None = None

    # ------------------------------------------------------------------
    # Public integration contract
    # ------------------------------------------------------------------
    def validate_pending_signal(
        self,
        signal: ScenarioSignal,
        snapshot: PrimitiveSnapshot,
    ) -> str | None:
        """Reject a one-bar-delayed signal if its causal leg no longer exists."""

        if str(signal.family).upper() != "OLAR":
            return None
        details = dict(signal.details)
        context_id = str(details.get("bias_context_id", ""))
        leg_id = int(details.get("olar_leg_id", -1))
        bias = self._bias
        leg = self._active_leg
        if bias is None or bias.context_id != context_id:
            return "OLAR_HTF_CONTEXT_INVALIDATED_BEFORE_ENTRY"
        if leg is None or leg.context_id != context_id or leg.leg_id != leg_id:
            return "OLAR_DIRECTIONAL_LEG_SUPERSEDED_BEFORE_ENTRY"
        if not leg.reserved_for(signal.scenario_id):
            return "OLAR_ENTRY_SIGNAL_NO_LONGER_RESERVED_BY_ACTIVE_LEG"
        if self._has_position_exit(context_id=context_id, direction=signal.direction):
            return "OLAR_STRUCTURAL_EXIT_PRECEDED_ENTRY"
        if snapshot.index <= int(details.get("olar_signal_index", -1)):
            return "OLAR_ENTRY_REQUIRES_LATER_COMPLETED_BAR"
        return None

    def pop_position_exit_for(
        self,
        *,
        context_id: str | None,
        direction: str,
    ) -> dict[str, Any] | None:
        """Return one matching structural exit request for a live OLAR position."""

        if not context_id:
            return None
        for index, request in enumerate(self._pending_position_exits):
            if request["context_id"] == context_id and request["direction"] == direction:
                return self._pending_position_exits.pop(index)
        return None

    def diagnostics_snapshot(self) -> dict[str, Any]:
        return {
            "active_bias_context_id": None if self._bias is None else self._bias.context_id,
            "active_leg": None if self._active_leg is None else self._active_leg.details(),
            "objective_lifecycle_enabled": self._one_use_objectives,
            "objective_ledger": self._objective_ledger.snapshot(),
            "pending_position_exits": [dict(value) for value in self._pending_position_exits],
            "last_control_decision": (
                None
                if self._last_control_decision is None
                else {
                    "classification": self._last_control_decision.classification,
                    **dict(self._last_control_decision.details),
                }
            ),
        }

    # ------------------------------------------------------------------
    # Causal observation order
    # ------------------------------------------------------------------
    def observe(self, snapshot: PrimitiveSnapshot, *, allow_new: bool) -> ScenarioStep:
        transitions: list[ScenarioTransition] = []
        signal: ScenarioSignal | None = None
        observation = snapshot.observation

        # Target-side objectives must be removed before any response can arm an
        # entry on this completed bar. The opposite side remains visible until
        # after sweep detection on the same bar.
        target_side = self._target_side(self._bias.direction) if self._bias is not None else None
        if self._one_use_objectives and target_side is not None:
            touched = self._objective_ledger.observe_completed_bar(
                index=snapshot.index,
                high=observation.high,
                low=observation.low,
                sides={target_side},
            )
            transitions.extend(self._record_objective_touches(touched, snapshot))
            self._close_unreserved_leg_if_objective_reached(touched, snapshot, transitions)

        completed_control = self._accumulate_control(snapshot)
        completed_bias = self._accumulate(snapshot, period=self._bias_period, kind="bias")
        completed_liquidity = self._accumulate(
            snapshot,
            period=self._liquidity_period,
            kind="liquidity",
        )

        # First apply the inherited HTF accepted-boundary invalidation. This is
        # not a control-auction hypothesis and remains unchanged.
        if self._bias is not None:
            prior_context = self._bias.context_id
            prior_direction = self._bias.direction
            context_step = HierarchicalMultiLiquidityEngine._advance_bias(self, snapshot)
            transitions.extend(context_step.transitions)
            if self._bias is None:
                self._queue_position_exit(
                    context_id=prior_context,
                    direction=prior_direction,
                    reason="OLAR_HTF_ACCEPTED_BOUNDARY_LOST",
                    snapshot=snapshot,
                    details={},
                )
                transitions.extend(
                    self._close_active_leg(
                        snapshot,
                        "HTF_ACCEPTED_BOUNDARY_LOST",
                        invalidate_objective=True,
                    ),
                )

        # At a shared 60m/15m boundary, the newly completed HTF auction owns the
        # state transition. Its final 15m sub-auction cannot independently renew
        # or invalidate a context which did not yet exist before that close.
        if completed_bias is not None:
            transitions.extend(self._evaluate_completed_bias(completed_bias, snapshot))
            self._append_bias_history(completed_bias)
        elif completed_control is not None and self._bias is not None:
            transitions.extend(self._process_completed_control(completed_control, snapshot))

        if completed_control is not None:
            self._append_control_history(completed_control)

        if self._sweep is not None:
            sweep_step = self._advance_sweep(snapshot, allow_new=allow_new)
            transitions.extend(sweep_step.transitions)
            signal = sweep_step.signal

        if (
            signal is None
            and self._bias is not None
            and self._active_leg is not None
            and self._sweep is None
            and allow_new
            and snapshot.index >= self._cooldown_until
        ):
            started = self._maybe_start_sweep(snapshot)
            if started is not None:
                transitions.append(started)

        if completed_liquidity is not None:
            self._liquidity_history.append(completed_liquidity)
            if len(self._liquidity_history) > 16:
                self._liquidity_history = self._liquidity_history[-16:]
            self._confirm_liquidity_pools()
            self._sync_pool_objectives(snapshot)

        # Consume every remaining objective touched by this completed bar after
        # sweep detection. This makes the initiating swept pool one-use without
        # suppressing the sweep which consumes it.
        if self._one_use_objectives:
            touched = self._objective_ledger.observe_completed_bar(
                index=snapshot.index,
                high=observation.high,
                low=observation.low,
            )
            transitions.extend(self._record_objective_touches(touched, snapshot))
            self._close_unreserved_leg_if_objective_reached(touched, snapshot, transitions)

        return ScenarioStep(transitions=tuple(transitions), signal=signal)

    # ------------------------------------------------------------------
    # HTF context and 15m control-auction lifecycle
    # ------------------------------------------------------------------
    def _evaluate_completed_bias(
        self,
        bar: _AuctionBar,
        snapshot: PrimitiveSnapshot,
    ) -> tuple[ScenarioTransition, ...]:
        previous = self._bias
        previous_context = None if previous is None else previous.context_id
        previous_direction = None if previous is None else previous.direction
        transitions = list(super()._evaluate_completed_bias(bar, snapshot))
        bias = self._bias
        if bias is None or bias.context_id == previous_context:
            return tuple(transitions)

        if previous_context is not None and previous_direction is not None:
            self._queue_position_exit(
                context_id=previous_context,
                direction=previous_direction,
                reason="OLAR_HTF_CONTEXT_REPLACED",
                snapshot=snapshot,
                details={"replacement_context_id": bias.context_id},
            )
            transitions.extend(
                self._close_active_leg(
                    snapshot,
                    "HTF_CONTEXT_REPLACED",
                    invalidate_objective=True,
                ),
            )

        transitions.extend(
            self._create_directional_leg(
                bias=bias,
                origin=bar.open,
                extreme=bar.high if bias.direction == "LONG" else bar.low,
                snapshot=snapshot,
                reason="HTF_ACCEPTANCE_CREATED_INITIAL_DIRECTIONAL_LEG",
                source="HTF_ACCEPTANCE",
            ),
        )
        return tuple(transitions)

    def _process_completed_control(
        self,
        auction: ControlAuction,
        snapshot: PrimitiveSnapshot,
    ) -> tuple[ScenarioTransition, ...]:
        bias = self._bias
        if bias is None:
            return ()
        atr_bars = int(self.params.get("olar_control_atr_bars", 12))
        volume_bars = int(self.params.get("olar_control_volume_bars", 12))
        lookback = int(self.params.get("olar_control_breakout_lookback", 4))
        required = max(atr_bars, volume_bars, lookback)
        if len(self._control_history) < required:
            return ()

        atr = sum(self._control_true_ranges[-atr_bars:]) / atr_bars
        baseline_volume = median(self._control_volumes[-volume_bars:])
        prior = self._control_history[-lookback:]
        thresholds = ControlThresholds(
            acceptance_atr=float(
                self.params.get(
                    "olar_control_acceptance_close_atr",
                    self.params.get("hsc_bias_acceptance_close_atr", 0.02),
                ),
            ),
            minimum_range_atr=float(
                self.params.get(
                    "olar_control_range_atr",
                    self.params.get("hsc_bias_range_atr", 0.75),
                ),
            ),
            minimum_body_fraction=float(
                self.params.get(
                    "olar_control_body_fraction",
                    self.params.get("hsc_bias_body_fraction", 0.50),
                ),
            ),
            minimum_relative_volume=float(
                self.params.get(
                    "olar_control_relative_volume",
                    self.params.get("hsc_bias_relative_volume", 0.95),
                ),
            ),
            minimum_flow_ratio=float(
                self.params.get(
                    "olar_control_flow_ratio",
                    self.params.get("hsc_bias_flow_ratio", 0.04),
                ),
            ),
            outer_close_location=float(
                self.params.get(
                    "olar_control_close_location",
                    self.params.get("hsc_bias_close_location", 0.68),
                ),
            ),
            use_flow=bool(
                self.params.get(
                    "olar_control_use_flow",
                    self.params.get("hff_use_bias_flow", True),
                ),
            ),
        )
        decision = classify_control_auction(
            direction=bias.direction,
            auction=auction,
            prior_high=max(value.high for value in prior),
            prior_low=min(value.low for value in prior),
            atr=atr,
            baseline_volume=baseline_volume,
            leg_origin=None if self._active_leg is None else self._active_leg.origin,
            thresholds=thresholds,
        )
        self._last_control_decision = decision
        if decision.classification == "NO_CONTROL_STATE_CHANGE":
            return ()

        transitions: list[ScenarioTransition] = []
        details = {
            **dict(decision.details),
            "bias_context_id": bias.context_id,
            "control_period_minutes": self._control_period,
            "active_leg": None if self._active_leg is None else self._active_leg.details(),
        }

        if decision.opposing_acceptance:
            context_id = bias.context_id
            direction = bias.direction
            transitions.append(
                self._control_transition(
                    context_id=context_id,
                    previous_state="BIAS_ACTIVE",
                    next_state="RESET",
                    reason=decision.classification,
                    reference_price=auction.close,
                    details=details,
                ),
            )
            self._queue_position_exit(
                context_id=context_id,
                direction=direction,
                reason="OLAR_OPPOSING_CONTROL_AUCTION_ACCEPTED",
                snapshot=snapshot,
                details=details,
            )
            transitions.extend(
                self._close_active_leg(
                    snapshot,
                    decision.classification,
                    invalidate_objective=True,
                ),
            )
            transitions.extend(
                self._reset_bias_and_sweep(
                    snapshot,
                    "OPPOSING_CONTROL_AUCTION_ACCEPTED",
                ).transitions,
            )
            return tuple(transitions)

        if decision.same_direction_renewal:
            if self._sweep is not None:
                transitions.append(
                    self._sweep_transition(
                        self._sweep,
                        self._sweep.state,
                        "RESET",
                        "SAME_DIRECTION_CONTROL_AUCTION_RENEWED_LEG",
                        auction.close,
                        details,
                    ),
                )
                self._sweep = None
            transitions.extend(
                self._close_active_leg(
                    snapshot,
                    "SAME_DIRECTION_CONTROL_AUCTION_RENEWED_LEG",
                    invalidate_objective=True,
                ),
            )
            transitions.extend(
                self._create_directional_leg(
                    bias=bias,
                    origin=auction.open,
                    extreme=auction.high if bias.direction == "LONG" else auction.low,
                    snapshot=snapshot,
                    reason=decision.classification,
                    source="CONTROL_AUCTION",
                ),
            )
            return tuple(transitions)

        if decision.leg_origin_lost:
            transitions.append(
                self._control_transition(
                    context_id=bias.context_id,
                    previous_state="LEG_ACTIVE",
                    next_state="LEG_SUSPENDED",
                    reason=decision.classification,
                    reference_price=auction.close,
                    details=details,
                ),
            )
            self._queue_position_exit(
                context_id=bias.context_id,
                direction=bias.direction,
                reason="OLAR_ACTIVE_DIRECTIONAL_LEG_ORIGIN_LOST",
                snapshot=snapshot,
                details=details,
            )
            if self._sweep is not None:
                transitions.append(
                    self._sweep_transition(
                        self._sweep,
                        self._sweep.state,
                        "RESET",
                        decision.classification,
                        auction.close,
                        details,
                    ),
                )
                self._sweep = None
            transitions.extend(
                self._close_active_leg(
                    snapshot,
                    decision.classification,
                    invalidate_objective=True,
                ),
            )
        return tuple(transitions)

    def _create_directional_leg(
        self,
        *,
        bias: _Bias,
        origin: float,
        extreme: float,
        snapshot: PrimitiveSnapshot,
        reason: str,
        source: str,
    ) -> tuple[ScenarioTransition, ...]:
        self._leg_sequence += 1
        side = self._target_side(bias.direction)
        key = ObjectiveKey(
            kind="ACTIVE_LEG_EXTREME",
            side=side,
            source_id=f"{bias.context_id}:{self._leg_sequence}",
        )
        state = self._objective_ledger.register(
            key,
            level=extreme,
            reason="ACTIVE_DIRECTIONAL_LEG_EXTREME",
            confirmed_index=snapshot.index,
            confirmed_ts_ns=snapshot.observation.ts_ns,
            metadata={
                "bias_context_id": bias.context_id,
                "leg_id": self._leg_sequence,
                "leg_source": source,
            },
        )
        self._active_leg = DirectionalLeg(
            context_id=bias.context_id,
            leg_id=self._leg_sequence,
            direction=bias.direction,
            origin=float(origin),
            extreme=float(extreme),
            created_index=snapshot.index,
            created_ts_ns=snapshot.observation.ts_ns,
            objective_key=key,
        )
        return (
            self._control_transition(
                context_id=bias.context_id,
                previous_state="IDLE",
                next_state="LEG_ACTIVE",
                reason=reason,
                reference_price=origin,
                details={
                    "leg": self._active_leg.details(),
                    "objective": state.details(),
                },
            ),
        )

    def _close_active_leg(
        self,
        snapshot: PrimitiveSnapshot,
        reason: str,
        *,
        invalidate_objective: bool,
    ) -> tuple[ScenarioTransition, ...]:
        leg = self._active_leg
        if leg is None:
            return ()
        if invalidate_objective and self._one_use_objectives:
            self._objective_ledger.invalidate(
                leg.objective_key,
                index=snapshot.index,
                reason=reason,
            )
        leg.close(index=snapshot.index, reason=reason)
        transition = self._control_transition(
            context_id=leg.context_id,
            previous_state="LEG_ACTIVE",
            next_state="LEG_CLOSED",
            reason=reason,
            reference_price=snapshot.observation.close,
            details={"leg": leg.details()},
        )
        self._active_leg = None
        return (transition,)

    # ------------------------------------------------------------------
    # Objective lifecycle and target selection
    # ------------------------------------------------------------------
    @staticmethod
    def _target_side(direction: str) -> str:
        return "UPPER" if direction == "LONG" else "LOWER"

    def _pool_objective_key(self, pool: _LiquidityPool) -> ObjectiveKey:
        key = self._pool_key(pool)
        kind = self._pool_kinds.get(key, "CONFIRMED_SWING")
        return ObjectiveKey(
            kind="LTF_LIQUIDITY_POOL",
            side=pool.side,
            source_id=f"{kind}:{pool.side}:{pool.source_ts_ns}",
        )

    def _pool_target_reason(self, pool: _LiquidityPool) -> str:
        kind = self._pool_kinds.get(self._pool_key(pool), "CONFIRMED_SWING")
        if pool.side == "UPPER":
            return (
                "EQUAL_LTF_BUYSIDE_LIQUIDITY"
                if kind == "EQUAL_HIGH"
                else "CONFIRMED_LTF_BUYSIDE_LIQUIDITY"
            )
        return (
            "EQUAL_LTF_SELLSIDE_LIQUIDITY"
            if kind == "EQUAL_LOW"
            else "CONFIRMED_LTF_SELLSIDE_LIQUIDITY"
        )

    def _sync_pool_objectives(self, snapshot: PrimitiveSnapshot) -> None:
        for pool in self._liquidity_pools:
            key = self._pool_objective_key(pool)
            kind = self._pool_kinds.get(self._pool_key(pool), "CONFIRMED_SWING")
            state = self._objective_ledger.register(
                key,
                level=pool.level,
                reason=self._pool_target_reason(pool),
                confirmed_index=snapshot.index,
                confirmed_ts_ns=pool.confirmed_ts_ns,
                metadata={
                    "pool_kind": kind,
                    "pool_touches": self._pool_touches.get(self._pool_key(pool), 1),
                    "pool_source_ts_ns": pool.source_ts_ns,
                    "pool_confirmed_ts_ns": pool.confirmed_ts_ns,
                },
            )
            self._known_pool_objectives.add(state.key)

    def _candidate_objectives(self, direction: str, entry: float) -> list[_ObjectiveChoice]:
        if self._one_use_objectives:
            states = self._objective_ledger.available_for_direction(
                direction=direction,
                entry=entry,
                kinds={"LTF_LIQUIDITY_POOL", "ACTIVE_LEG_EXTREME"},
            )
            return [
                _ObjectiveChoice(
                    key=state.key,
                    level=state.level,
                    reason=state.reason,
                    details=state.details(),
                )
                for state in states
            ]

        # Controlled diagnostic ablation: the leg lifecycle and structural exits
        # remain unchanged, but confirmed pool objectives can be reused across
        # later legs exactly as in the failed parent HML target implementation.
        side = self._target_side(direction)
        choices: list[_ObjectiveChoice] = []
        seen: set[tuple[ObjectiveKey, float]] = set()
        for pool in self._liquidity_pools:
            if pool.side != side:
                continue
            beyond = pool.level > entry if direction == "LONG" else pool.level < entry
            if not beyond:
                continue
            key = self._pool_objective_key(pool)
            marker = (key, float(pool.level))
            if marker in seen:
                continue
            seen.add(marker)
            choices.append(
                _ObjectiveChoice(
                    key=key,
                    level=float(pool.level),
                    reason=self._pool_target_reason(pool),
                    details={
                        "ablation_reusable": True,
                        "pool_kind": self._pool_kinds.get(
                            self._pool_key(pool),
                            "CONFIRMED_SWING",
                        ),
                    },
                ),
            )
        leg = self._active_leg
        if leg is not None and leg.context_id == (None if self._bias is None else self._bias.context_id):
            beyond = leg.extreme > entry if direction == "LONG" else leg.extreme < entry
            if beyond:
                choices.append(
                    _ObjectiveChoice(
                        key=leg.objective_key,
                        level=leg.extreme,
                        reason="ACTIVE_DIRECTIONAL_LEG_EXTREME",
                        details={"ablation_reusable": False, "leg": leg.details()},
                    ),
                )
        choices.sort(key=lambda value: abs(value.level - entry))
        return choices

    def _select_objective(
        self,
        *,
        direction: str,
        entry: float,
        stop: float,
    ) -> _ObjectiveChoice | None:
        risk = abs(entry - stop)
        if risk <= 0.0:
            return None
        minimum_rr = float(self.params.get("minimum_structural_rr", 0.75))
        valid: list[_ObjectiveChoice] = []
        for choice in self._candidate_objectives(direction, entry):
            reward = choice.level - entry if direction == "LONG" else entry - choice.level
            if reward > 0.0 and reward / risk >= minimum_rr:
                valid.append(choice)
        valid.sort(key=lambda value: abs(value.level - entry))
        return valid[0] if valid else None

    def _maybe_start_sweep(self, snapshot: PrimitiveSnapshot) -> ScenarioTransition | None:
        bias = self._bias
        leg = self._active_leg
        if (
            bias is None
            or leg is None
            or leg.context_id != bias.context_id
            or leg.direction != bias.direction
            or not leg.open_for_entry
            or snapshot.index <= leg.created_index
            or self._has_position_exit(context_id=bias.context_id, direction=bias.direction)
        ):
            return None

        if self._one_use_objectives:
            sweep_side = "LOWER" if bias.direction == "LONG" else "UPPER"
            for pool in self._liquidity_pools:
                if pool.side != sweep_side:
                    continue
                state = self._objective_ledger.get(self._pool_objective_key(pool))
                if state is not None and not state.available:
                    self._consumed_levels.add((pool.source_ts_ns, bias.direction))

        transition = super()._maybe_start_sweep(snapshot)
        if transition is None:
            return None
        return replace(
            transition,
            details={
                **dict(transition.details),
                "olar_leg": leg.details(),
                "objective_lifecycle_enabled": self._one_use_objectives,
            },
        )

    def _emit(self, snapshot: PrimitiveSnapshot, bias: _Bias, sweep: _SweepEpisode) -> ScenarioStep:
        observation = snapshot.observation
        leg = self._active_leg
        if (
            leg is None
            or leg.context_id != bias.context_id
            or leg.direction != sweep.direction
            or not leg.open_for_entry
        ):
            transition = self._sweep_transition(
                sweep,
                sweep.state,
                "RESET",
                "OLAR_DIRECTIONAL_LEG_NOT_AVAILABLE_AT_EMIT",
                observation.close,
                {},
            )
            self._sweep = None
            return ScenarioStep(transitions=(transition,))

        buffer_value = float(self.params.get("hsc_stop_buffer_atr_htf", 0.025)) * bias.atr_htf
        stop = (
            sweep.sweep_low - buffer_value
            if sweep.direction == "LONG"
            else sweep.sweep_high + buffer_value
        )
        choice = self._select_objective(
            direction=sweep.direction,
            entry=observation.close,
            stop=stop,
        )
        if choice is None:
            transition = self._sweep_transition(
                sweep,
                sweep.state,
                "RESET",
                "NO_UNRESOLVED_OBJECTIVE_WITH_SUFFICIENT_SPACE",
                observation.close,
                {"leg": leg.details()},
            )
            self._sweep = None
            return ScenarioStep(transitions=(transition,))

        if self._one_use_objectives and not self._objective_ledger.reserve(
            choice.key,
            index=snapshot.index,
        ):
            transition = self._sweep_transition(
                sweep,
                sweep.state,
                "RESET",
                "SELECTED_OBJECTIVE_BECAME_UNAVAILABLE",
                observation.close,
                {"objective": dict(choice.details)},
            )
            self._sweep = None
            return ScenarioStep(transitions=(transition,))
        if not leg.reserve_entry(scenario_id=sweep.scenario_id, index=snapshot.index):
            transition = self._sweep_transition(
                sweep,
                sweep.state,
                "RESET",
                "DIRECTIONAL_LEG_ALREADY_RESERVED",
                observation.close,
                {"leg": leg.details()},
            )
            self._sweep = None
            return ScenarioStep(transitions=(transition,))

        swept_key = (
            "LOWER" if sweep.direction == "LONG" else "UPPER",
            sweep.level_ts_ns,
        )
        transition_details = {
            "direction": sweep.direction,
            "bias_context_id": bias.context_id,
            "olar_leg_id": leg.leg_id,
            "olar_leg_origin": leg.origin,
            "olar_leg_extreme": leg.extreme,
            "swept_level": sweep.level,
            "swept_pool_kind": self._pool_kinds.get(swept_key, "CONFIRMED_SWING"),
            "swept_pool_touches": self._pool_touches.get(swept_key, 1),
            "impulse_position": sweep.impulse_position,
            "sweep_flow_ratio": sweep.sweep_flow_ratio,
            "stop_price": stop,
            "target_price": choice.level,
            "target_reason": choice.reason,
            "target_objective": dict(choice.details),
            "objective_lifecycle_enabled": self._one_use_objectives,
        }
        transition = self._sweep_transition(
            sweep,
            sweep.state,
            "ENTRY_ARMED",
            "OLAR_UNRESOLVED_OBJECTIVE_PATH_CONFIRMED",
            observation.close,
            transition_details,
        )
        signal = ScenarioSignal(
            scenario_id=sweep.scenario_id,
            family="OLAR",
            direction=sweep.direction,
            observed_ts_ns=observation.ts_ns,
            reference_entry=observation.close,
            stop_price=stop,
            target_price=choice.level,
            target_reason=choice.reason,
            atr=snapshot.atr,
            liquidity_level=sweep.level,
            details={
                **transition_details,
                "olar_signal_index": snapshot.index,
                "bias_period_minutes": self._bias_period,
                "control_period_minutes": self._control_period,
                "liquidity_period_minutes": self._liquidity_period,
                "bias_boundary": bias.boundary,
                "bias_origin": bias.origin,
                "bias_atr_htf": bias.atr_htf,
                "flow_stage_contract": {
                    "bias": self._stage_flag("hff_use_bias_flow"),
                    "sweep": self._stage_flag("hff_use_sweep_flow"),
                    "response": self._stage_flag("hff_use_response_flow"),
                    "control": bool(
                        self.params.get(
                            "olar_control_use_flow",
                            self.params.get("hff_use_bias_flow", True),
                        ),
                    ),
                },
                "pool_family_contract": self.params.get(
                    "hml_pool_families",
                    "SWING_AND_EQUAL",
                ),
                "leg": leg.details(),
            },
        )
        self._sweep = None
        self._cooldown_until = snapshot.index + int(self.params.get("hsc_cooldown_bars", 2))
        return ScenarioStep(transitions=(transition,), signal=signal)

    # ------------------------------------------------------------------
    # Objective events, exits, clocks, and transitions
    # ------------------------------------------------------------------
    def _record_objective_touches(
        self,
        touched: tuple[ObjectiveState, ...],
        snapshot: PrimitiveSnapshot,
    ) -> tuple[ScenarioTransition, ...]:
        return tuple(
            ScenarioTransition(
                scenario_id=f"OLAR-OBJECTIVE-{state.key.source_id}",
                event_type="OLAR_OBJECTIVE_TRANSITION",
                previous_state="AVAILABLE",
                next_state="CONSUMED",
                reason_code=str(state.terminal_reason),
                reference_price=state.level,
                details={
                    **state.details(),
                    "observation_ts_ns": snapshot.observation.ts_ns,
                },
            )
            for state in touched
        )

    def _close_unreserved_leg_if_objective_reached(
        self,
        touched: tuple[ObjectiveState, ...],
        snapshot: PrimitiveSnapshot,
        transitions: list[ScenarioTransition],
    ) -> None:
        leg = self._active_leg
        if leg is None or leg.reserved_scenario_id is not None:
            return
        if any(state.key == leg.objective_key for state in touched):
            transitions.extend(
                self._close_active_leg(
                    snapshot,
                    "ACTIVE_LEG_OBJECTIVE_REACHED_BEFORE_ENTRY",
                    invalidate_objective=False,
                ),
            )
            if self._sweep is not None:
                transitions.append(
                    self._sweep_transition(
                        self._sweep,
                        self._sweep.state,
                        "RESET",
                        "ACTIVE_LEG_OBJECTIVE_REACHED_BEFORE_ENTRY",
                        snapshot.observation.close,
                        {},
                    ),
                )
                self._sweep = None

    def _queue_position_exit(
        self,
        *,
        context_id: str,
        direction: str,
        reason: str,
        snapshot: PrimitiveSnapshot,
        details: Mapping[str, Any],
    ) -> None:
        request = {
            "context_id": str(context_id),
            "direction": str(direction),
            "reason": str(reason),
            "requested_index": snapshot.index,
            "requested_ts_ns": snapshot.observation.ts_ns,
            "reference_price": snapshot.observation.close,
            "details": dict(details),
        }
        marker = (request["context_id"], request["direction"], request["reason"])
        existing = {
            (value["context_id"], value["direction"], value["reason"])
            for value in self._pending_position_exits
        }
        if marker not in existing:
            self._pending_position_exits.append(request)
            if len(self._pending_position_exits) > 32:
                self._pending_position_exits = self._pending_position_exits[-32:]

    def _has_position_exit(self, *, context_id: str, direction: str) -> bool:
        return any(
            value["context_id"] == context_id and value["direction"] == direction
            for value in self._pending_position_exits
        )

    def _accumulate_control(self, snapshot: PrimitiveSnapshot) -> ControlAuction | None:
        observation = snapshot.observation
        source = source_bar_datetime(observation.ts_ns)
        source_minute = int(source.timestamp() // 60)
        bucket = source_minute // self._control_period
        position = source_minute % self._control_period
        current = self._control_current
        if current is None or int(current["bucket"]) != bucket:
            current = {
                "bucket": bucket,
                "open": observation.open,
                "high": observation.high,
                "low": observation.low,
                "close": observation.close,
                "volume": observation.volume,
                "taker_buy_volume": observation.taker_buy_volume,
            }
            self._control_current = current
        else:
            current["high"] = max(float(current["high"]), observation.high)
            current["low"] = min(float(current["low"]), observation.low)
            current["close"] = observation.close
            current["volume"] = float(current["volume"]) + observation.volume
            current["taker_buy_volume"] = (
                float(current["taker_buy_volume"]) + observation.taker_buy_volume
            )
        if position != self._control_period - 1:
            return None
        completed = ControlAuction(
            open=float(current["open"]),
            high=float(current["high"]),
            low=float(current["low"]),
            close=float(current["close"]),
            volume=float(current["volume"]),
            taker_buy_volume=float(current["taker_buy_volume"]),
            end_ts_ns=observation.ts_ns,
        )
        self._control_current = None
        return completed

    def _append_control_history(self, auction: ControlAuction) -> None:
        previous_close = self._control_history[-1].close if self._control_history else auction.close
        true_range = max(
            auction.high - auction.low,
            abs(auction.high - previous_close),
            abs(auction.low - previous_close),
        )
        self._control_history.append(auction)
        self._control_true_ranges.append(true_range)
        self._control_volumes.append(auction.volume)
        capacity = max(
            48,
            int(self.params.get("olar_control_atr_bars", 12)) + 8,
            int(self.params.get("olar_control_volume_bars", 12)) + 8,
            int(self.params.get("olar_control_breakout_lookback", 4)) + 8,
        )
        if len(self._control_history) > capacity:
            self._control_history = self._control_history[-capacity:]
            self._control_true_ranges = self._control_true_ranges[-capacity:]
            self._control_volumes = self._control_volumes[-capacity:]

    def abort_active(self, snapshot: PrimitiveSnapshot, reason: str) -> ScenarioStep:
        transitions = list(super().abort_active(snapshot, reason).transitions)
        transitions.extend(
            self._close_active_leg(
                snapshot,
                reason,
                invalidate_objective=True,
            ),
        )
        return ScenarioStep(transitions=tuple(transitions))

    @staticmethod
    def _control_transition(
        *,
        context_id: str,
        previous_state: str,
        next_state: str,
        reason: str,
        reference_price: float | None,
        details: Mapping[str, Any],
    ) -> ScenarioTransition:
        return ScenarioTransition(
            scenario_id=f"OLAR-CONTROL-{context_id}",
            event_type="OLAR_CONTROL_TRANSITION",
            previous_state=previous_state,
            next_state=next_state,
            reason_code=reason,
            reference_price=reference_price,
            details=dict(details),
        )
