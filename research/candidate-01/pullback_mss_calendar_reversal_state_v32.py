"""Accepted pullback -> completed MSS -> calendar-liquidity reversal.

V31 found a weakly positive immediate reversal after the first completed
counterflow pullback, but a later one-tick boundary-loss trigger selected only
whipsaws.  V32 preserves the frequent outside-flow initiative and accepted
pullback states, then requires a *completed equal-notional event* to close
through both the accepted boundary and the completed pullback's internal swing.

Primary additionally requires aggressive flow on that completed MSS event to
agree with the reversal.  The single control removes only this flow-agreement
requirement.  Both variants use the same first-later-TradeTick market entry,
pullback-swing invalidation, causal completed-day/week external-liquidity
target, 3% current-NAV risk, cost contract and maximum hold.

This module emits immutable plans and diagnostics only.  It contains no order
matching, fee, PnL, margin or NAV simulation.
"""
from __future__ import annotations

from dataclasses import dataclass

from causal_calendar_liquidity_v22 import (
    CalendarTargetSelection,
    CausalCalendarLiquidityBook,
)
from core import Side
from impact_elasticity_resumption_v28_nautilus_week import (
    InitiativeSetup,
    SIDE_COST_BUFFER_FRACTION,
    _one_bar_elasticity,
)
from impact_regime_probe import EventFeature, ScenarioPlan
from impact_resumption_stop_state_v29 import PullbackResumptionEntryStateMachine


@dataclass(frozen=True, slots=True)
class PullbackMssDecision:
    primary_scenario_id: str | None
    control_scenario_id: str
    source_scenario_id: str
    signal_index: int
    signal_time_ns: int
    source_side: str
    reversal_side: str
    accepted_boundary: float
    pullback_high: float
    pullback_low: float
    mss_break_level: float
    mss_close: float
    mss_imbalance_z: float | None
    flow_aligned: bool
    stop_price: float
    local_structure_edge: float
    target_level_id: str
    target_period: str
    target_period_key: str
    target_price: float
    target_available_time_ns: int
    price_risk_bps_at_signal: float
    gross_reward_bps_at_signal: float
    reason_code: str


class PullbackMssCalendarReversalStateMachine(
    PullbackResumptionEntryStateMachine,
):
    """Resolve accepted pullbacks with a completed reversal MSS."""

    def __init__(self) -> None:
        super().__init__()
        # PullbackResumptionEntryStateMachine aliases market_plans to plans.  In
        # v32 that canonical plan stream is the primary aligned-flow rule.
        self.primary_plans: list[ScenarioPlan] = self.plans
        self.close_only_control_plans: list[ScenarioPlan] = []
        self.calendar_book = CausalCalendarLiquidityBook()
        self.target_selections: list[CalendarTargetSelection] = []
        self.mss_decisions: list[PullbackMssDecision] = []
        # v29 execution intents are deliberately unused in v32.
        self.stop_instructions.clear()
        self.entry_decisions.clear()

    @staticmethod
    def _reversal_side(setup: InitiativeSetup) -> Side:
        return Side.SHORT if setup.side is Side.LONG else Side.LONG

    @staticmethod
    def _pullback_stop(setup: InitiativeSetup) -> float:
        if setup.pullback_high is None or setup.pullback_low is None:
            raise RuntimeError("v32 MSS requires a completed pullback swing")
        return (
            float(setup.pullback_high) * (1.0 + SIDE_COST_BUFFER_FRACTION)
            if setup.side is Side.LONG
            else float(setup.pullback_low) * (1.0 - SIDE_COST_BUFFER_FRACTION)
        )

    @staticmethod
    def _mss_break_level(setup: InitiativeSetup) -> float:
        if setup.pullback_high is None or setup.pullback_low is None:
            raise RuntimeError("v32 MSS requires a completed pullback swing")
        # A reversal close must cross both the accepted external boundary and
        # the completed counterflow swing.  min/max expresses both constraints
        # without a fitted distance threshold.
        return (
            min(float(setup.boundary), float(setup.pullback_low))
            if setup.side is Side.LONG
            else max(float(setup.boundary), float(setup.pullback_high))
        )

    @staticmethod
    def _mss_closed(
        setup: InitiativeSetup,
        feature: EventFeature,
    ) -> bool:
        level = PullbackMssCalendarReversalStateMachine._mss_break_level(setup)
        return (
            float(feature.bar.close) < level
            if setup.side is Side.LONG
            else float(feature.bar.close) > level
        )

    @staticmethod
    def _invalidation_touched(
        setup: InitiativeSetup,
        feature: EventFeature,
    ) -> bool:
        stop = PullbackMssCalendarReversalStateMachine._pullback_stop(setup)
        return (
            float(feature.bar.high) >= stop
            if setup.side is Side.LONG
            else float(feature.bar.low) <= stop
        )

    def _select_calendar_target(
        self,
        *,
        setup: InitiativeSetup,
        signal_time_ns: int,
        scenario_id: str,
    ) -> CalendarTargetSelection | None:
        reversal_side = self._reversal_side(setup)
        local_edge = (
            float(setup.structure_low)
            if reversal_side is Side.SHORT
            else float(setup.structure_high)
        )
        return self.calendar_book.select_target(
            scenario_id=scenario_id,
            signal_time_ns=signal_time_ns,
            side=reversal_side,
            local_internal_pivot=self._mss_break_level(setup),
            local_intermediate_pivot=local_edge,
        )

    @staticmethod
    def _plan(
        *,
        scenario_id: str,
        setup: InitiativeSetup,
        feature: EventFeature,
        index: int,
        target: float,
        reason_code: str,
    ) -> ScenarioPlan:
        reversal_side = (
            Side.SHORT if setup.side is Side.LONG else Side.LONG
        )
        stop = PullbackMssCalendarReversalStateMachine._pullback_stop(setup)
        hold = PullbackMssCalendarReversalStateMachine._mss_break_level(setup)
        return ScenarioPlan(
            scenario_id=scenario_id,
            response="REVERSAL",
            side=reversal_side,
            signal_bar_index=index,
            signal_time_ns=int(feature.bar.end_time_ns),
            stop_price=float(stop),
            target_price=float(target),
            confirmation_hold_price=float(hold),
            structure_high=max(
                float(setup.structure_high),
                float(stop),
                float(target),
            ),
            structure_low=min(
                float(setup.structure_low),
                float(stop),
                float(target),
            ),
            structure_midpoint=0.5 * (
                float(setup.structure_high) + float(setup.structure_low)
            ),
            pulse_high=float(setup.path_high),
            pulse_low=float(setup.path_low),
            pulse_flow_score=float(setup.pulse_flow_score),
            pulse_move_atr=0.0,
            pulse_path_efficiency=float(setup.pulse_efficiency),
            pulse_close_location=0.0,
            reason_code=reason_code,
        )

    def _emit_mss(
        self,
        *,
        setup: InitiativeSetup,
        feature: EventFeature,
        index: int,
        event_elasticity: float | None,
    ) -> None:
        reversal_side = self._reversal_side(setup)
        break_level = self._mss_break_level(setup)
        aligned_z = self._aligned_z(reversal_side, feature)
        flow_aligned = aligned_z is not None and aligned_z > 0.0
        control_id = setup.scenario_id + f":pullback-mss-close-control:{index}"
        primary_id = (
            setup.scenario_id + f":pullback-mss-flow:{index}"
            if flow_aligned
            else None
        )
        selection = self._select_calendar_target(
            setup=setup,
            signal_time_ns=int(feature.bar.end_time_ns),
            scenario_id=primary_id or control_id,
        )
        if selection is None:
            self.counts[
                "mss_without_active_calendar_target_beyond_local_structure"
            ] += 1
            self._transition(
                setup=setup,
                feature=feature,
                index=index,
                event_type="INVALIDATED",
                reason_code="NO_CAUSAL_CALENDAR_TARGET_BEYOND_LOCAL_STRUCTURE",
                aligned_close_change=None,
                event_elasticity=event_elasticity,
            )
            return

        stop = self._pullback_stop(setup)
        target = float(selection.target_price)
        entry = float(feature.bar.close)
        geometry_ok = (
            stop < entry < target
            if reversal_side is Side.LONG
            else target < entry < stop
        )
        if not geometry_ok:
            self.counts["mss_calendar_plan_geometry_invalid"] += 1
            self._transition(
                setup=setup,
                feature=feature,
                index=index,
                event_type="INVALIDATED",
                reason_code="PULLBACK_MSS_CALENDAR_GEOMETRY_INVALID",
                aligned_close_change=None,
                event_elasticity=event_elasticity,
            )
            return

        control_plan = self._plan(
            scenario_id=control_id,
            setup=setup,
            feature=feature,
            index=index,
            target=target,
            reason_code=(
                "PULLBACK_MSS_CLOSE_TO_CALENDAR_EXTERNAL_LIQUIDITY_CONTROL"
            ),
        )
        self.close_only_control_plans.append(control_plan)
        self.counts["mss_close_control_plans"] += 1

        if flow_aligned:
            assert primary_id is not None
            primary_plan = self._plan(
                scenario_id=primary_id,
                setup=setup,
                feature=feature,
                index=index,
                target=target,
                reason_code=(
                    "PULLBACK_MSS_ALIGNED_FLOW_TO_CALENDAR_EXTERNAL_LIQUIDITY"
                ),
            )
            self.primary_plans.append(primary_plan)
            self.counts["mss_aligned_flow_primary_plans"] += 1
        else:
            self.counts["mss_close_without_aligned_aggressive_flow"] += 1

        self.target_selections.append(selection)
        local_edge = (
            float(setup.structure_low)
            if reversal_side is Side.SHORT
            else float(setup.structure_high)
        )
        price_risk = abs(entry - stop)
        gross_reward = abs(target - entry)
        self.mss_decisions.append(
            PullbackMssDecision(
                primary_scenario_id=primary_id,
                control_scenario_id=control_id,
                source_scenario_id=setup.scenario_id,
                signal_index=index,
                signal_time_ns=int(feature.bar.end_time_ns),
                source_side=setup.side.value,
                reversal_side=reversal_side.value,
                accepted_boundary=float(setup.boundary),
                pullback_high=float(setup.pullback_high),
                pullback_low=float(setup.pullback_low),
                mss_break_level=float(break_level),
                mss_close=entry,
                mss_imbalance_z=(
                    float(feature.imbalance_z)
                    if feature.imbalance_z is not None
                    else None
                ),
                flow_aligned=flow_aligned,
                stop_price=float(stop),
                local_structure_edge=local_edge,
                target_level_id=selection.target_level_id,
                target_period=selection.target_period,
                target_period_key=selection.target_period_key,
                target_price=target,
                target_available_time_ns=int(selection.target_available_time_ns),
                price_risk_bps_at_signal=price_risk / entry * 10_000.0,
                gross_reward_bps_at_signal=gross_reward / entry * 10_000.0,
                reason_code=(
                    "ALIGNED_FLOW_PRIMARY_AND_CLOSE_ONLY_CONTROL"
                    if flow_aligned
                    else "CLOSE_ONLY_CONTROL_NO_FLOW_AGREEMENT"
                ),
            ),
        )
        self._transition(
            setup=setup,
            feature=feature,
            index=index,
            event_type="PULLBACK_MSS_RESOLVED",
            reason_code=(
                "COMPLETED_MSS_WITH_ALIGNED_AGGRESSIVE_FLOW"
                if flow_aligned
                else "COMPLETED_MSS_WITHOUT_ALIGNED_AGGRESSIVE_FLOW"
            ),
            aligned_close_change=None,
            event_elasticity=event_elasticity,
        )

    def _update_active(
        self,
        *,
        index: int,
        feature: EventFeature,
        previous: EventFeature,
        features: list[EventFeature] | None = None,
    ) -> None:
        if features is None:
            raise RuntimeError("v32 update requires completed feature history")
        remaining: list[InitiativeSetup] = []
        event_elasticity = _one_bar_elasticity(feature, previous)
        for setup in self.active:
            if index <= setup.created_index:
                remaining.append(setup)
                continue

            previous_close = float(previous.bar.close)
            source_aligned_change = setup.side.sign * (
                float(feature.bar.close) - previous_close
            )
            source_aligned_z = self._aligned_z(setup.side, feature)

            if index > setup.expiry_index:
                self.counts["response_window_expired"] += 1
                self._transition(
                    setup=setup,
                    feature=feature,
                    index=index,
                    event_type="INVALIDATED",
                    reason_code="PULLBACK_MSS_RESPONSE_WINDOW_EXPIRED",
                    aligned_close_change=source_aligned_change,
                    event_elasticity=event_elasticity,
                )
                continue

            if setup.pullback_index is None:
                setup.path_high = max(setup.path_high, float(feature.bar.high))
                setup.path_low = min(setup.path_low, float(feature.bar.low))
                if not self._outside_holds(setup, feature):
                    self.counts["outside_value_lost_before_pullback"] += 1
                    self._transition(
                        setup=setup,
                        feature=feature,
                        index=index,
                        event_type="INVALIDATED",
                        reason_code="OUTSIDE_VALUE_LOST_BEFORE_ACCEPTED_PULLBACK",
                        aligned_close_change=source_aligned_change,
                        event_elasticity=event_elasticity,
                    )
                    continue
                if self._target_touched(setup, feature):
                    self.counts["initiative_target_consumed_before_pullback"] += 1
                    self._transition(
                        setup=setup,
                        feature=feature,
                        index=index,
                        event_type="INVALIDATED",
                        reason_code="INITIATIVE_EXPANSION_CONSUMED_BEFORE_PULLBACK",
                        aligned_close_change=source_aligned_change,
                        event_elasticity=event_elasticity,
                    )
                    continue
                counterflow_pullback = (
                    source_aligned_z is not None
                    and source_aligned_z < 0.0
                    and source_aligned_change < 0.0
                    and event_elasticity is not None
                )
                if counterflow_pullback:
                    setup.pullback_index = index
                    setup.pullback_time_ns = int(feature.bar.end_time_ns)
                    setup.pullback_high = float(feature.bar.high)
                    setup.pullback_low = float(feature.bar.low)
                    setup.adverse_elasticity = float(event_elasticity)
                    self.counts["accepted_counterflow_pullbacks"] += 1
                    self._transition(
                        setup=setup,
                        feature=feature,
                        index=index,
                        event_type="PULLBACK_ACCEPTED_WAIT_MSS",
                        reason_code=(
                            "COUNTERFLOW_PULLBACK_OUTSIDE_VALUE_HELD_WAIT_MSS"
                        ),
                        aligned_close_change=source_aligned_change,
                        event_elasticity=event_elasticity,
                    )
                    remaining.append(setup)
                    continue
                remaining.append(setup)
                continue

            # The completed first pullback is now frozen.  Later bars cannot
            # move the stop or internal swing.  They can only invalidate it,
            # complete the reversal MSS, or allow the response window to end.
            if self._invalidation_touched(setup, feature):
                self.counts["pullback_swing_invalidated_before_mss"] += 1
                self._transition(
                    setup=setup,
                    feature=feature,
                    index=index,
                    event_type="INVALIDATED",
                    reason_code="PULLBACK_OUTSIDE_EXTREME_FAILED_BEFORE_MSS",
                    aligned_close_change=source_aligned_change,
                    event_elasticity=event_elasticity,
                )
                continue
            if self._target_touched(setup, feature):
                self.counts["initiative_target_consumed_before_mss"] += 1
                self._transition(
                    setup=setup,
                    feature=feature,
                    index=index,
                    event_type="INVALIDATED",
                    reason_code="INITIATIVE_EXPANSION_CONSUMED_BEFORE_REVERSAL_MSS",
                    aligned_close_change=source_aligned_change,
                    event_elasticity=event_elasticity,
                )
                continue
            if not self._mss_closed(setup, feature):
                remaining.append(setup)
                continue

            reversal_side = self._reversal_side(setup)
            reversal_change = reversal_side.sign * (
                float(feature.bar.close) - previous_close
            )
            if reversal_change <= 0.0:
                # A close through the fixed level should normally be aligned;
                # keep this explicit guard as an implementation invariant.
                self.counts["mss_close_without_reversal_price_change"] += 1
                self._transition(
                    setup=setup,
                    feature=feature,
                    index=index,
                    event_type="INVALIDATED",
                    reason_code="MSS_LEVEL_CROSSED_WITHOUT_REVERSAL_PRICE_CHANGE",
                    aligned_close_change=reversal_change,
                    event_elasticity=event_elasticity,
                )
                continue
            self._emit_mss(
                setup=setup,
                feature=feature,
                index=index,
                event_elasticity=event_elasticity,
            )
        self.active = remaining

    def on_feature(
        self,
        *,
        index: int,
        features: list[EventFeature],
    ) -> None:
        feature = features[index]
        self.calendar_book.on_bar(feature.bar)
        if index <= 0:
            return
        previous = features[index - 1]
        self._update_active(
            index=index,
            feature=feature,
            previous=previous,
            features=features,
        )
        self._try_arm(index=index, features=features)
        elasticity = _one_bar_elasticity(feature, previous)
        if elasticity is not None:
            self.elasticity_history.append(float(elasticity))


__all__ = [
    "PullbackMssCalendarReversalStateMachine",
    "PullbackMssDecision",
]
