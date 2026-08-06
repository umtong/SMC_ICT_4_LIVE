"""Two-sided completed pullback-auction resolver.

V30 assumed continuation after an outside initiative and accepted pullback.
V31 assumed reversal. V32 waited for a completed reversal MSS. None produced a
robust first-week edge. V33 removes the directional prior entirely.

After the same causal outside-flow initiative and first completed counterflow
pullback which preserves outside value, two mutually exclusive branches are
kept alive:

* continuation: a later completed event closes beyond the frozen pullback
  extreme in the initiative direction;
* reversal: a later completed event closes through both the accepted boundary
  and the opposite frozen pullback extreme.

Each branch has its own genuine invalidation at the opposite pullback extreme
plus one 7-bp side-cost buffer. A branch can die without killing the other. The
first completed structural resolution wins. If that resolution belongs to an
already invalidated branch, the setup ends without trading. The primary rule
also requires aggressive flow on the resolution event to agree with the
resolved direction. The single control removes only this flow agreement.

The destination is the nearest causally active, unconsumed completed-day/week
external-liquidity level beyond the pre-initiative structure edge in the
resolved direction. This module emits plans and diagnostics only; NautilusTrader
owns all orders, fills, costs, margin, positions, PnL and NAV.
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


@dataclass(slots=True)
class BranchViability:
    continuation: bool = True
    reversal: bool = True


@dataclass(frozen=True, slots=True)
class TwoSidedResolutionDecision:
    primary_scenario_id: str | None
    control_scenario_id: str | None
    source_scenario_id: str
    signal_index: int
    signal_time_ns: int
    source_side: str
    resolved_side: str | None
    resolution_type: str
    structural_resolution: bool
    branch_was_viable: bool
    same_event_invalidation: bool
    flow_aligned: bool | None
    accepted_boundary: float
    pullback_high: float
    pullback_low: float
    continuation_break_level: float
    reversal_break_level: float
    continuation_stop: float
    reversal_stop: float
    resolution_close: float
    resolution_imbalance_z: float | None
    target_level_id: str | None
    target_period: str | None
    target_period_key: str | None
    target_price: float | None
    target_available_time_ns: int | None
    price_risk_bps_at_signal: float | None
    gross_reward_bps_at_signal: float | None
    reason_code: str


class TwoSidedPullbackAuctionStateMachine(PullbackResumptionEntryStateMachine):
    """First completed continuation/reversal resolution after pullback wins."""

    def __init__(self) -> None:
        super().__init__()
        self.primary_plans: list[ScenarioPlan] = self.plans
        self.close_only_control_plans: list[ScenarioPlan] = []
        self.calendar_book = CausalCalendarLiquidityBook()
        self.target_selections: list[CalendarTargetSelection] = []
        self.resolution_decisions: list[TwoSidedResolutionDecision] = []
        self.branch_viability: dict[str, BranchViability] = {}
        self.stop_instructions.clear()
        self.entry_decisions.clear()

    @staticmethod
    def _resolved_side(setup: InitiativeSetup, resolution_type: str) -> Side:
        if resolution_type == "CONTINUATION":
            return setup.side
        if resolution_type == "REVERSAL":
            return Side.SHORT if setup.side is Side.LONG else Side.LONG
        raise ValueError(f"unknown resolution type {resolution_type}")

    @staticmethod
    def _continuation_break(setup: InitiativeSetup) -> float:
        if setup.pullback_high is None or setup.pullback_low is None:
            raise RuntimeError("v33 requires a completed pullback")
        return (
            float(setup.pullback_high)
            if setup.side is Side.LONG
            else float(setup.pullback_low)
        )

    @staticmethod
    def _reversal_break(setup: InitiativeSetup) -> float:
        if setup.pullback_high is None or setup.pullback_low is None:
            raise RuntimeError("v33 requires a completed pullback")
        return (
            min(float(setup.boundary), float(setup.pullback_low))
            if setup.side is Side.LONG
            else max(float(setup.boundary), float(setup.pullback_high))
        )

    @staticmethod
    def _branch_stop(setup: InitiativeSetup, resolved_side: Side) -> float:
        if setup.pullback_high is None or setup.pullback_low is None:
            raise RuntimeError("v33 requires a completed pullback")
        return (
            float(setup.pullback_low) * (1.0 - SIDE_COST_BUFFER_FRACTION)
            if resolved_side is Side.LONG
            else float(setup.pullback_high) * (1.0 + SIDE_COST_BUFFER_FRACTION)
        )

    @classmethod
    def _structural_resolution(
        cls,
        setup: InitiativeSetup,
        feature: EventFeature,
    ) -> str | None:
        close = float(feature.bar.close)
        continuation = (
            close > cls._continuation_break(setup)
            if setup.side is Side.LONG
            else close < cls._continuation_break(setup)
        )
        reversal = (
            close < cls._reversal_break(setup)
            if setup.side is Side.LONG
            else close > cls._reversal_break(setup)
        )
        if continuation and reversal:
            raise RuntimeError(
                f"v33 mutually exclusive resolution violated: {setup.scenario_id}",
            )
        if continuation:
            return "CONTINUATION"
        if reversal:
            return "REVERSAL"
        return None

    @classmethod
    def _stop_touched(
        cls,
        setup: InitiativeSetup,
        feature: EventFeature,
        *,
        resolution_type: str,
    ) -> bool:
        side = cls._resolved_side(setup, resolution_type)
        stop = cls._branch_stop(setup, side)
        return (
            float(feature.bar.low) <= stop
            if side is Side.LONG
            else float(feature.bar.high) >= stop
        )

    @classmethod
    def _confirmation_hold(
        cls,
        setup: InitiativeSetup,
        resolution_type: str,
    ) -> float:
        return (
            cls._continuation_break(setup)
            if resolution_type == "CONTINUATION"
            else cls._reversal_break(setup)
        )

    def _select_target(
        self,
        *,
        setup: InitiativeSetup,
        signal_time_ns: int,
        scenario_id: str,
        resolved_side: Side,
    ) -> CalendarTargetSelection | None:
        local_edge = (
            float(setup.structure_high)
            if resolved_side is Side.LONG
            else float(setup.structure_low)
        )
        return self.calendar_book.select_target(
            scenario_id=scenario_id,
            signal_time_ns=signal_time_ns,
            side=resolved_side,
            local_internal_pivot=self._confirmation_hold(
                setup,
                "CONTINUATION" if resolved_side is setup.side else "REVERSAL",
            ),
            local_intermediate_pivot=local_edge,
        )

    @classmethod
    def _plan(
        cls,
        *,
        scenario_id: str,
        setup: InitiativeSetup,
        feature: EventFeature,
        index: int,
        resolution_type: str,
        target: float,
        reason_code: str,
    ) -> ScenarioPlan:
        side = cls._resolved_side(setup, resolution_type)
        stop = cls._branch_stop(setup, side)
        return ScenarioPlan(
            scenario_id=scenario_id,
            response=resolution_type,
            side=side,
            signal_bar_index=index,
            signal_time_ns=int(feature.bar.end_time_ns),
            stop_price=float(stop),
            target_price=float(target),
            confirmation_hold_price=float(
                cls._confirmation_hold(setup, resolution_type)
            ),
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

    def _record_resolution_without_trade(
        self,
        *,
        setup: InitiativeSetup,
        feature: EventFeature,
        index: int,
        resolution_type: str,
        branch_was_viable: bool,
        same_event_invalidation: bool,
        reason_code: str,
    ) -> None:
        resolved_side = self._resolved_side(setup, resolution_type)
        aligned_z = self._aligned_z(resolved_side, feature)
        self.resolution_decisions.append(
            TwoSidedResolutionDecision(
                primary_scenario_id=None,
                control_scenario_id=None,
                source_scenario_id=setup.scenario_id,
                signal_index=index,
                signal_time_ns=int(feature.bar.end_time_ns),
                source_side=setup.side.value,
                resolved_side=resolved_side.value,
                resolution_type=resolution_type,
                structural_resolution=True,
                branch_was_viable=branch_was_viable,
                same_event_invalidation=same_event_invalidation,
                flow_aligned=(aligned_z is not None and aligned_z > 0.0),
                accepted_boundary=float(setup.boundary),
                pullback_high=float(setup.pullback_high),
                pullback_low=float(setup.pullback_low),
                continuation_break_level=self._continuation_break(setup),
                reversal_break_level=self._reversal_break(setup),
                continuation_stop=self._branch_stop(setup, setup.side),
                reversal_stop=self._branch_stop(
                    setup,
                    Side.SHORT if setup.side is Side.LONG else Side.LONG,
                ),
                resolution_close=float(feature.bar.close),
                resolution_imbalance_z=(
                    float(feature.imbalance_z)
                    if feature.imbalance_z is not None
                    else None
                ),
                target_level_id=None,
                target_period=None,
                target_period_key=None,
                target_price=None,
                target_available_time_ns=None,
                price_risk_bps_at_signal=None,
                gross_reward_bps_at_signal=None,
                reason_code=reason_code,
            ),
        )
        self._transition(
            setup=setup,
            feature=feature,
            index=index,
            event_type="RESOLUTION_REJECTED",
            reason_code=reason_code,
            aligned_close_change=None,
            event_elasticity=None,
        )

    def _emit_resolution(
        self,
        *,
        setup: InitiativeSetup,
        feature: EventFeature,
        index: int,
        resolution_type: str,
        event_elasticity: float | None,
    ) -> None:
        viability = self.branch_viability[setup.scenario_id]
        branch_was_viable = (
            viability.continuation
            if resolution_type == "CONTINUATION"
            else viability.reversal
        )
        same_event_invalidation = self._stop_touched(
            setup,
            feature,
            resolution_type=resolution_type,
        )
        if not branch_was_viable:
            self.counts["first_resolution_on_previously_invalid_branch"] += 1
            self._record_resolution_without_trade(
                setup=setup,
                feature=feature,
                index=index,
                resolution_type=resolution_type,
                branch_was_viable=False,
                same_event_invalidation=same_event_invalidation,
                reason_code="FIRST_RESOLUTION_BELONGED_TO_INVALIDATED_BRANCH",
            )
            return
        if same_event_invalidation:
            self.counts["resolution_and_stop_same_completed_event"] += 1
            self._record_resolution_without_trade(
                setup=setup,
                feature=feature,
                index=index,
                resolution_type=resolution_type,
                branch_was_viable=True,
                same_event_invalidation=True,
                reason_code="RESOLUTION_AND_INVALIDATION_AMBIGUOUS_SAME_EVENT",
            )
            return

        side = self._resolved_side(setup, resolution_type)
        base_id = (
            setup.scenario_id
            + f":first-{resolution_type.lower()}-resolution:{index}"
        )
        selection = self._select_target(
            setup=setup,
            signal_time_ns=int(feature.bar.end_time_ns),
            scenario_id=base_id,
            resolved_side=side,
        )
        if selection is None:
            self.counts["resolution_without_active_calendar_target"] += 1
            self._record_resolution_without_trade(
                setup=setup,
                feature=feature,
                index=index,
                resolution_type=resolution_type,
                branch_was_viable=True,
                same_event_invalidation=False,
                reason_code="NO_CAUSAL_CALENDAR_TARGET_BEYOND_RESOLVED_STRUCTURE",
            )
            return

        stop = self._branch_stop(setup, side)
        target = float(selection.target_price)
        entry = float(feature.bar.close)
        geometry_ok = (
            stop < entry < target
            if side is Side.LONG
            else target < entry < stop
        )
        if not geometry_ok:
            self.counts["two_sided_resolution_geometry_invalid"] += 1
            self._record_resolution_without_trade(
                setup=setup,
                feature=feature,
                index=index,
                resolution_type=resolution_type,
                branch_was_viable=True,
                same_event_invalidation=False,
                reason_code="TWO_SIDED_RESOLUTION_CALENDAR_GEOMETRY_INVALID",
            )
            return

        control_id = base_id + ":close-control"
        aligned_z = self._aligned_z(side, feature)
        flow_aligned = aligned_z is not None and aligned_z > 0.0
        primary_id = base_id + ":flow-primary" if flow_aligned else None
        control_plan = self._plan(
            scenario_id=control_id,
            setup=setup,
            feature=feature,
            index=index,
            resolution_type=resolution_type,
            target=target,
            reason_code=(
                f"FIRST_{resolution_type}_CLOSE_TO_CALENDAR_LIQUIDITY_CONTROL"
            ),
        )
        self.close_only_control_plans.append(control_plan)
        self.counts[
            f"{resolution_type.lower()}_close_control_plans"
        ] += 1

        if flow_aligned:
            assert primary_id is not None
            primary_plan = self._plan(
                scenario_id=primary_id,
                setup=setup,
                feature=feature,
                index=index,
                resolution_type=resolution_type,
                target=target,
                reason_code=(
                    f"FIRST_{resolution_type}_ALIGNED_FLOW_TO_"
                    "CALENDAR_EXTERNAL_LIQUIDITY"
                ),
            )
            self.primary_plans.append(primary_plan)
            self.counts[
                f"{resolution_type.lower()}_aligned_flow_primary_plans"
            ] += 1
        else:
            self.counts[
                f"{resolution_type.lower()}_without_aligned_flow"
            ] += 1

        self.target_selections.append(selection)
        price_risk = abs(entry - stop)
        gross_reward = abs(target - entry)
        self.resolution_decisions.append(
            TwoSidedResolutionDecision(
                primary_scenario_id=primary_id,
                control_scenario_id=control_id,
                source_scenario_id=setup.scenario_id,
                signal_index=index,
                signal_time_ns=int(feature.bar.end_time_ns),
                source_side=setup.side.value,
                resolved_side=side.value,
                resolution_type=resolution_type,
                structural_resolution=True,
                branch_was_viable=True,
                same_event_invalidation=False,
                flow_aligned=flow_aligned,
                accepted_boundary=float(setup.boundary),
                pullback_high=float(setup.pullback_high),
                pullback_low=float(setup.pullback_low),
                continuation_break_level=self._continuation_break(setup),
                reversal_break_level=self._reversal_break(setup),
                continuation_stop=self._branch_stop(setup, setup.side),
                reversal_stop=self._branch_stop(
                    setup,
                    Side.SHORT if setup.side is Side.LONG else Side.LONG,
                ),
                resolution_close=entry,
                resolution_imbalance_z=(
                    float(feature.imbalance_z)
                    if feature.imbalance_z is not None
                    else None
                ),
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
            event_type="TWO_SIDED_PULLBACK_RESOLVED",
            reason_code=(
                f"FIRST_COMPLETED_{resolution_type}_RESOLUTION_"
                + ("WITH" if flow_aligned else "WITHOUT")
                + "_ALIGNED_FLOW"
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
            raise RuntimeError("v33 update requires completed feature history")
        remaining: list[InitiativeSetup] = []
        event_elasticity = _one_bar_elasticity(feature, previous)
        for setup in self.active:
            if index <= setup.created_index:
                remaining.append(setup)
                continue

            previous_close = float(previous.bar.close)
            source_change = setup.side.sign * (
                float(feature.bar.close) - previous_close
            )
            source_z = self._aligned_z(setup.side, feature)

            if index > setup.expiry_index:
                self.counts["response_window_expired"] += 1
                self.branch_viability.pop(setup.scenario_id, None)
                self._transition(
                    setup=setup,
                    feature=feature,
                    index=index,
                    event_type="INVALIDATED",
                    reason_code="TWO_SIDED_PULLBACK_RESPONSE_WINDOW_EXPIRED",
                    aligned_close_change=source_change,
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
                        aligned_close_change=source_change,
                        event_elasticity=event_elasticity,
                    )
                    continue
                counterflow_pullback = (
                    source_z is not None
                    and source_z < 0.0
                    and source_change < 0.0
                    and event_elasticity is not None
                )
                if counterflow_pullback:
                    setup.pullback_index = index
                    setup.pullback_time_ns = int(feature.bar.end_time_ns)
                    setup.pullback_high = float(feature.bar.high)
                    setup.pullback_low = float(feature.bar.low)
                    setup.adverse_elasticity = float(event_elasticity)
                    self.branch_viability[setup.scenario_id] = BranchViability()
                    self.counts["accepted_counterflow_pullbacks"] += 1
                    self._transition(
                        setup=setup,
                        feature=feature,
                        index=index,
                        event_type="PULLBACK_ACCEPTED_TWO_SIDED",
                        reason_code=(
                            "COUNTERFLOW_PULLBACK_OUTSIDE_VALUE_HELD_"
                            "WAIT_FIRST_COMPLETED_RESOLUTION"
                        ),
                        aligned_close_change=source_change,
                        event_elasticity=event_elasticity,
                    )
                remaining.append(setup)
                continue

            resolution_type = self._structural_resolution(setup, feature)
            if resolution_type is not None:
                self._emit_resolution(
                    setup=setup,
                    feature=feature,
                    index=index,
                    resolution_type=resolution_type,
                    event_elasticity=event_elasticity,
                )
                self.branch_viability.pop(setup.scenario_id, None)
                continue

            viability = self.branch_viability[setup.scenario_id]
            continuation_touched = self._stop_touched(
                setup,
                feature,
                resolution_type="CONTINUATION",
            )
            reversal_touched = self._stop_touched(
                setup,
                feature,
                resolution_type="REVERSAL",
            )
            if viability.continuation and continuation_touched:
                viability.continuation = False
                self.counts["continuation_branch_invalidated"] += 1
                self._transition(
                    setup=setup,
                    feature=feature,
                    index=index,
                    event_type="BRANCH_INVALIDATED",
                    reason_code="CONTINUATION_PULLBACK_STOP_TOUCHED",
                    aligned_close_change=source_change,
                    event_elasticity=event_elasticity,
                )
            if viability.reversal and reversal_touched:
                viability.reversal = False
                self.counts["reversal_branch_invalidated"] += 1
                self._transition(
                    setup=setup,
                    feature=feature,
                    index=index,
                    event_type="BRANCH_INVALIDATED",
                    reason_code="REVERSAL_PULLBACK_STOP_TOUCHED",
                    aligned_close_change=source_change,
                    event_elasticity=event_elasticity,
                )
            if not viability.continuation and not viability.reversal:
                self.counts["both_resolution_branches_invalidated"] += 1
                self.branch_viability.pop(setup.scenario_id, None)
                continue
            remaining.append(setup)
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
    "BranchViability",
    "TwoSidedPullbackAuctionStateMachine",
    "TwoSidedResolutionDecision",
]
