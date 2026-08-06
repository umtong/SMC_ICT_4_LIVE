"""Causal failed-acceptance reversal after an outside-flow pullback.

V30 established that an outside initiative followed by a counterflow pullback
is frequent and executable, but treating every accepted pullback as continuation
lost money.  V31 keeps the detector and completed-pullback state unchanged and
changes only the directional resolution:

* an initiative remains merely a liquidity-discovery observation;
* a completed counterflow pullback must still close outside the accepted edge;
* the primary waits for a later venue trade to cross back through that edge;
* the crossing arms a reversal STOP_LIMIT order in NautilusTrader;
* invalidation is beyond the completed pullback's outside extreme;
* the target is the opposite edge of the pre-initiative completed structure.

The single control enters the same reversal immediately after the completed
pullback.  It removes only the accepted-boundary-loss confirmation.  This module
contains no fill, fee, PnL, margin or NAV simulation.
"""
from __future__ import annotations

from dataclasses import dataclass

from core import Side
from impact_elasticity_resumption_v28_nautilus_week import (
    InitiativeSetup,
    SIDE_COST_BUFFER_FRACTION,
)
from impact_regime_probe import EventFeature, ScenarioPlan
from impact_resumption_stop_state_v29 import (
    BTC_TICK_SIZE,
    STOP_LIMIT_PROTECTION_FRACTION,
    PullbackResumptionEntryStateMachine,
    causal_event_duration_ns,
    next_tick_above,
    next_tick_below,
)
from nautilus_tick_stop_plan_backtest import StopEntryInstruction


@dataclass(frozen=True, slots=True)
class FailedAcceptanceDecision:
    scenario_id: str
    control_scenario_id: str
    source_scenario_id: str
    signal_index: int
    signal_time_ns: int
    source_side: str
    trade_side: str
    accepted_boundary: float
    pullback_high: float
    pullback_low: float
    trigger_price: float
    worst_limit_price: float
    stop_price: float
    target_price: float
    expiry_time_ns: int
    remaining_response_events: int
    causal_median_event_duration_ns: int
    initiative_elasticity: float
    adverse_elasticity: float
    reason_code: str


class FailedAcceptanceReversalStateMachine(PullbackResumptionEntryStateMachine):
    """Accepted pullback -> later boundary loss -> failed-auction reversal."""

    def __init__(self) -> None:
        super().__init__()
        self.immediate_control_plans: list[ScenarioPlan] = []
        self.entry_decisions: list[FailedAcceptanceDecision] = []

    @staticmethod
    def _reversal_side(setup: InitiativeSetup) -> Side:
        return Side.SHORT if setup.side is Side.LONG else Side.LONG

    @staticmethod
    def _target_touched(
        setup: InitiativeSetup,
        feature: EventFeature,
    ) -> bool:
        """Discard if the reversal destination was consumed before entry."""

        return (
            float(feature.bar.low) <= float(setup.structure_low)
            if setup.side is Side.LONG
            else float(feature.bar.high) >= float(setup.structure_high)
        )

    @staticmethod
    def _stop_and_target(setup: InitiativeSetup) -> tuple[float, float]:
        if setup.pullback_high is None or setup.pullback_low is None:
            raise RuntimeError("v31 reversal requires a completed pullback swing")
        if setup.side is Side.LONG:
            stop = float(setup.pullback_high) * (
                1.0 + SIDE_COST_BUFFER_FRACTION
            )
            target = float(setup.structure_low)
        else:
            stop = float(setup.pullback_low) * (
                1.0 - SIDE_COST_BUFFER_FRACTION
            )
            target = float(setup.structure_high)
        return float(stop), float(target)

    @staticmethod
    def _trigger_and_limit(setup: InitiativeSetup) -> tuple[float, float]:
        if setup.side is Side.LONG:
            trigger = next_tick_below(float(setup.boundary))
            limit_price = trigger * (1.0 - STOP_LIMIT_PROTECTION_FRACTION)
        else:
            trigger = next_tick_above(float(setup.boundary))
            limit_price = trigger * (1.0 + STOP_LIMIT_PROTECTION_FRACTION)
        return float(trigger), float(limit_price)

    def _arm_conditional_entry(
        self,
        *,
        setup: InitiativeSetup,
        feature: EventFeature,
        index: int,
        features: list[EventFeature],
        event_elasticity: float,
    ) -> None:
        reversal_side = self._reversal_side(setup)
        stop, target = self._stop_and_target(setup)
        trigger, limit_price = self._trigger_and_limit(setup)
        geometry_ok = (
            stop < trigger <= limit_price < target
            if reversal_side is Side.LONG
            else target < limit_price <= trigger < stop
        )
        if not geometry_ok:
            self.counts["failed_acceptance_geometry_invalid"] += 1
            self._transition(
                setup=setup,
                feature=feature,
                index=index,
                event_type="INVALIDATED",
                reason_code="FAILED_ACCEPTANCE_REVERSAL_GEOMETRY_INVALID",
                aligned_close_change=None,
                event_elasticity=event_elasticity,
            )
            return

        remaining_events = max(int(setup.expiry_index) - index, 1)
        median_duration_ns = causal_event_duration_ns(features, index=index)
        expiry_time_ns = (
            int(feature.bar.end_time_ns)
            + remaining_events * median_duration_ns
        )
        scenario_id = setup.scenario_id + f":boundary-loss-reversal:{index}"
        control_scenario_id = scenario_id + ":immediate-control"

        # At arming time price still closes outside the accepted boundary.  The
        # completed pullback range is therefore the causal validity envelope;
        # the STOP_LIMIT trigger itself represents the later boundary loss.
        hold_price = (
            float(feature.bar.high)
            if reversal_side is Side.SHORT
            else float(feature.bar.low)
        )
        primary_plan = ScenarioPlan(
            scenario_id=scenario_id,
            response="REVERSAL",
            side=reversal_side,
            signal_bar_index=index,
            signal_time_ns=int(feature.bar.end_time_ns),
            stop_price=stop,
            target_price=target,
            confirmation_hold_price=hold_price,
            structure_high=float(setup.structure_high),
            structure_low=float(setup.structure_low),
            structure_midpoint=0.5 * (
                setup.structure_high + setup.structure_low
            ),
            pulse_high=float(setup.path_high),
            pulse_low=float(setup.path_low),
            pulse_flow_score=float(setup.pulse_flow_score),
            pulse_move_atr=0.0,
            pulse_path_efficiency=float(setup.pulse_efficiency),
            pulse_close_location=0.0,
            reason_code="ACCEPTED_BOUNDARY_LOSS_FAILED_AUCTION_REVERSAL",
        )
        control_plan = ScenarioPlan(
            scenario_id=control_scenario_id,
            response="REVERSAL",
            side=reversal_side,
            signal_bar_index=index,
            signal_time_ns=int(feature.bar.end_time_ns),
            stop_price=stop,
            target_price=target,
            confirmation_hold_price=hold_price,
            structure_high=float(setup.structure_high),
            structure_low=float(setup.structure_low),
            structure_midpoint=0.5 * (
                setup.structure_high + setup.structure_low
            ),
            pulse_high=float(setup.path_high),
            pulse_low=float(setup.path_low),
            pulse_flow_score=float(setup.pulse_flow_score),
            pulse_move_atr=0.0,
            pulse_path_efficiency=float(setup.pulse_efficiency),
            pulse_close_location=0.0,
            reason_code="ACCEPTED_PULLBACK_IMMEDIATE_REVERSAL_CONTROL",
        )
        instruction = StopEntryInstruction(
            plan=primary_plan,
            trigger_price=trigger,
            limit_price=limit_price,
            expiry_time_ns=expiry_time_ns,
            entry_reason="LOSE_ACCEPTED_BOUNDARY_AFTER_COUNTERFLOW_PULLBACK",
        )
        self.market_plans.append(primary_plan)
        self.immediate_control_plans.append(control_plan)
        self.stop_instructions.append(instruction)
        self.entry_decisions.append(
            FailedAcceptanceDecision(
                scenario_id=scenario_id,
                control_scenario_id=control_scenario_id,
                source_scenario_id=setup.scenario_id,
                signal_index=index,
                signal_time_ns=int(feature.bar.end_time_ns),
                source_side=setup.side.value,
                trade_side=reversal_side.value,
                accepted_boundary=float(setup.boundary),
                pullback_high=float(feature.bar.high),
                pullback_low=float(feature.bar.low),
                trigger_price=trigger,
                worst_limit_price=limit_price,
                stop_price=stop,
                target_price=target,
                expiry_time_ns=expiry_time_ns,
                remaining_response_events=remaining_events,
                causal_median_event_duration_ns=median_duration_ns,
                initiative_elasticity=float(setup.initiative_elasticity),
                adverse_elasticity=float(event_elasticity),
                reason_code=primary_plan.reason_code,
            ),
        )
        self.counts["counterflow_pullbacks"] += 1
        self.counts["boundary_loss_reversal_instructions"] += 1
        self.counts["immediate_reversal_control_plans"] += 1
        self._transition(
            setup=setup,
            feature=feature,
            index=index,
            event_type="FAILED_ACCEPTANCE_REVERSAL_ARMED",
            reason_code=primary_plan.reason_code,
            aligned_close_change=None,
            event_elasticity=event_elasticity,
        )


__all__ = [
    "BTC_TICK_SIZE",
    "STOP_LIMIT_PROTECTION_FRACTION",
    "FailedAcceptanceDecision",
    "FailedAcceptanceReversalStateMachine",
]
