#!/usr/bin/env python3
"""Intrinsic-time failed sweep -> MSS -> broken-pivot retest scenario.

This module restores the market-structure-shift state transition which was
missing from the earlier directional-change failed-sweep candidate.

The detector is unchanged in economic meaning:

* equal-notional event bars;
* a 40-bps directional-change threshold derived from the 14-bps round-trip
  execution contract and the fixed 65% minimum price-risk share;
* same-side liquidity is swept and the confirming directional change closes
  back inside it;
* initiative flow into the sweep and reversal flow away from it have opposite
  signs.

A sweep failure is only an observation.  It becomes a trade scenario after:

1. price closes through the nearest confirmed opposing pivot with aligned
   aggressive flow (MSS/CHoCH);
2. a later completed event retests that broken pivot from the new side and
   closes rejected with aligned aggressive flow;
3. the farther of the two pre-existing opposing pivots remains unconsumed.

The stop is beyond the adverse extreme of the post-MSS retest path plus the
unchanged 7-bps side-cost buffer.  This is local scenario invalidation: losing
the pullback which defended the broken pivot means the MSS was not accepted.
No fixed wall-clock expiry or fitted retracement ratio is used.  A setup expires
through state transitions: failed-boundary reacceptance, target consumption,
broken-pivot reacceptance, a contrary directional change, or supersession by a
newer failed sweep of the same side.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Literal

from core import Side
from directional_change_failed_sweep_week import (
    DIRECTIONAL_CHANGE_FRACTION,
    STOP_BUFFER_FRACTION,
    DirectionalChangeDetector,
    DirectionalChangeEvent,
)
from impact_regime_probe import EventFeature, ScenarioPlan


Phase = Literal["WAIT_MSS", "WAIT_RETEST"]


@dataclass(slots=True)
class MssRetestSetup:
    scenario_id: str
    side: Side
    phase: Phase
    created_index: int
    created_time_ns: int
    boundary: float
    internal_pivot: float
    external_target: float
    sweep_path_high: float
    sweep_path_low: float
    trend_flow_imbalance: float
    reversal_flow_imbalance: float
    mss_index: int | None = None
    mss_time_ns: int | None = None
    mss_close: float | None = None
    retest_path_high: float | None = None
    retest_path_low: float | None = None


@dataclass(frozen=True, slots=True)
class MssRetestTransition:
    scenario_id: str
    event_type: str
    event_index: int
    event_time_ns: int
    reason_code: str
    phase: str
    side: str
    boundary: float
    internal_pivot: float
    external_target: float
    sweep_path_high: float
    sweep_path_low: float
    mss_index: int | None
    mss_time_ns: int | None
    mss_close: float | None
    retest_path_high: float | None
    retest_path_low: float | None
    imbalance_z: float | None
    close: float


class DirectionalChangeMssRetestStateMachine:
    """Convert failed intrinsic sweeps into MSS-retest ScenarioPlans."""

    def __init__(self) -> None:
        self.detector = DirectionalChangeDetector(
            threshold_fraction=DIRECTIONAL_CHANGE_FRACTION,
        )
        self.high_events: list[DirectionalChangeEvent] = []
        self.low_events: list[DirectionalChangeEvent] = []
        self.active: list[MssRetestSetup] = []
        self.plans: list[ScenarioPlan] = []
        self.transitions: list[MssRetestTransition] = []
        self.counts: Counter[str] = Counter()

    def _transition(
        self,
        *,
        setup: MssRetestSetup,
        feature: EventFeature,
        index: int,
        event_type: str,
        reason_code: str,
    ) -> None:
        self.transitions.append(
            MssRetestTransition(
                scenario_id=setup.scenario_id,
                event_type=event_type,
                event_index=index,
                event_time_ns=int(feature.bar.end_time_ns),
                reason_code=reason_code,
                phase=setup.phase,
                side=setup.side.value,
                boundary=float(setup.boundary),
                internal_pivot=float(setup.internal_pivot),
                external_target=float(setup.external_target),
                sweep_path_high=float(setup.sweep_path_high),
                sweep_path_low=float(setup.sweep_path_low),
                mss_index=setup.mss_index,
                mss_time_ns=setup.mss_time_ns,
                mss_close=setup.mss_close,
                retest_path_high=setup.retest_path_high,
                retest_path_low=setup.retest_path_low,
                imbalance_z=feature.imbalance_z,
                close=float(feature.bar.close),
            ),
        )

    @staticmethod
    def _aligned_flow(side: Side, feature: EventFeature) -> bool:
        return (
            feature.imbalance_z is not None
            and side.sign * float(feature.imbalance_z) > 0.0
        )

    @staticmethod
    def _failed_boundary_lost(
        setup: MssRetestSetup,
        feature: EventFeature,
    ) -> bool:
        return (
            feature.bar.close <= setup.boundary
            if setup.side is Side.LONG
            else feature.bar.close >= setup.boundary
        )

    @staticmethod
    def _target_touched(
        setup: MssRetestSetup,
        feature: EventFeature,
    ) -> bool:
        return (
            feature.bar.high >= setup.external_target
            if setup.side is Side.LONG
            else feature.bar.low <= setup.external_target
        )

    @staticmethod
    def _mss_confirmed(
        setup: MssRetestSetup,
        feature: EventFeature,
    ) -> bool:
        crossed = (
            feature.bar.close > setup.internal_pivot
            if setup.side is Side.LONG
            else feature.bar.close < setup.internal_pivot
        )
        return crossed and DirectionalChangeMssRetestStateMachine._aligned_flow(
            setup.side,
            feature,
        )

    @staticmethod
    def _broken_pivot_retest(
        setup: MssRetestSetup,
        feature: EventFeature,
    ) -> bool:
        if not DirectionalChangeMssRetestStateMachine._aligned_flow(
            setup.side,
            feature,
        ):
            return False
        if setup.side is Side.LONG:
            return (
                feature.bar.low <= setup.internal_pivot
                and feature.bar.close > setup.internal_pivot
            )
        return (
            feature.bar.high >= setup.internal_pivot
            and feature.bar.close < setup.internal_pivot
        )

    @staticmethod
    def _broken_pivot_reaccepted(
        setup: MssRetestSetup,
        feature: EventFeature,
    ) -> bool:
        return (
            feature.bar.close <= setup.internal_pivot
            if setup.side is Side.LONG
            else feature.bar.close >= setup.internal_pivot
        )

    @staticmethod
    def _contrary_event(side: Side, event: DirectionalChangeEvent) -> bool:
        return (
            event.event_type == "DOWN"
            if side is Side.LONG
            else event.event_type == "UP"
        )

    def _invalidate_same_side(
        self,
        *,
        side: Side,
        feature: EventFeature,
        index: int,
    ) -> None:
        remaining: list[MssRetestSetup] = []
        for setup in self.active:
            if setup.side is side:
                self.counts["superseded_by_newer_failed_sweep"] += 1
                self._transition(
                    setup=setup,
                    feature=feature,
                    index=index,
                    event_type="INVALIDATED",
                    reason_code="SUPERSEDED_BY_NEWER_FAILED_SWEEP",
                )
            else:
                remaining.append(setup)
        self.active = remaining

    def _arm_from_event(
        self,
        *,
        event: DirectionalChangeEvent,
        feature: EventFeature,
    ) -> None:
        if event.event_type == "DOWN":
            if not self.high_events or len(self.low_events) < 2:
                self.counts["insufficient_confirmed_liquidity"] += 1
                self.high_events.append(event)
                return
            prior_same = self.high_events[-1]
            opposing = self.low_events[-2:]
            side = Side.SHORT
            boundary = float(prior_same.pivot_price)
            internal = max(float(item.pivot_price) for item in opposing)
            external = min(float(item.pivot_price) for item in opposing)
            sweep = event.pivot_price > prior_same.pivot_price
            reentered = event.confirmation_price < prior_same.pivot_price
            flow = (
                event.trend_flow_imbalance > 0.0
                and event.reversal_flow_imbalance < 0.0
            )
            hierarchy = external < internal < boundary
            self.high_events.append(event)
        else:
            if not self.low_events or len(self.high_events) < 2:
                self.counts["insufficient_confirmed_liquidity"] += 1
                self.low_events.append(event)
                return
            prior_same = self.low_events[-1]
            opposing = self.high_events[-2:]
            side = Side.LONG
            boundary = float(prior_same.pivot_price)
            internal = min(float(item.pivot_price) for item in opposing)
            external = max(float(item.pivot_price) for item in opposing)
            sweep = event.pivot_price < prior_same.pivot_price
            reentered = event.confirmation_price > prior_same.pivot_price
            flow = (
                event.trend_flow_imbalance < 0.0
                and event.reversal_flow_imbalance > 0.0
            )
            hierarchy = boundary < internal < external
            self.low_events.append(event)

        if not sweep:
            self.counts["no_same_side_liquidity_sweep"] += 1
            return
        if not reentered:
            self.counts["outside_value_retained"] += 1
            return
        if not flow:
            self.counts["order_flow_did_not_reverse"] += 1
            return
        if not hierarchy:
            self.counts["invalid_liquidity_hierarchy"] += 1
            return
        target_untouched = (
            feature.bar.low > external
            if side is Side.SHORT
            else feature.bar.high < external
        )
        if not target_untouched:
            self.counts["external_target_already_consumed"] += 1
            return

        self._invalidate_same_side(
            side=side,
            feature=feature,
            index=event.confirmation_index,
        )
        setup = MssRetestSetup(
            scenario_id=(
                f"dc-mss:{event.confirmation_index}:"
                f"{side.value.lower()}:{event.confirmation_time_ns}"
            ),
            side=side,
            phase="WAIT_MSS",
            created_index=int(event.confirmation_index),
            created_time_ns=int(event.confirmation_time_ns),
            boundary=boundary,
            internal_pivot=internal,
            external_target=external,
            sweep_path_high=float(event.path_high),
            sweep_path_low=float(event.path_low),
            trend_flow_imbalance=float(event.trend_flow_imbalance),
            reversal_flow_imbalance=float(event.reversal_flow_imbalance),
        )
        self.active.append(setup)
        self.counts["armed"] += 1
        self._transition(
            setup=setup,
            feature=feature,
            index=event.confirmation_index,
            event_type="ARMED",
            reason_code="FAILED_INTRINSIC_SWEEP_WAITING_FOR_MSS",
        )

    @staticmethod
    def _plan(
        setup: MssRetestSetup,
        feature: EventFeature,
        index: int,
    ) -> ScenarioPlan:
        if setup.retest_path_high is None or setup.retest_path_low is None:
            raise RuntimeError("retest path must exist before plan emission")
        stop = (
            setup.retest_path_low * (1.0 - STOP_BUFFER_FRACTION)
            if setup.side is Side.LONG
            else setup.retest_path_high * (1.0 + STOP_BUFFER_FRACTION)
        )
        return ScenarioPlan(
            scenario_id=setup.scenario_id + f":broken-pivot-retest:{index}",
            response="EXHAUSTION_REVERSAL",
            side=setup.side,
            signal_bar_index=index,
            signal_time_ns=int(feature.bar.end_time_ns),
            stop_price=float(stop),
            target_price=float(setup.external_target),
            confirmation_hold_price=float(setup.internal_pivot),
            structure_high=max(
                float(setup.sweep_path_high),
                float(setup.internal_pivot),
                float(setup.external_target),
            ),
            structure_low=min(
                float(setup.sweep_path_low),
                float(setup.internal_pivot),
                float(setup.external_target),
            ),
            structure_midpoint=0.5 * (
                float(setup.internal_pivot) + float(setup.external_target)
            ),
            pulse_high=float(setup.retest_path_high),
            pulse_low=float(setup.retest_path_low),
            pulse_flow_score=float(setup.reversal_flow_imbalance),
            pulse_move_atr=0.0,
            pulse_path_efficiency=0.0,
            pulse_close_location=0.0,
            reason_code="FAILED_SWEEP_MSS_BROKEN_PIVOT_RETEST_REJECTED",
        )

    def _manage_active(
        self,
        *,
        index: int,
        feature: EventFeature,
    ) -> list[ScenarioPlan]:
        emitted: list[ScenarioPlan] = []
        remaining: list[MssRetestSetup] = []
        for setup in self.active:
            if index <= setup.created_index:
                remaining.append(setup)
                continue

            if self._target_touched(setup, feature):
                self.counts["target_consumed_before_entry"] += 1
                self._transition(
                    setup=setup,
                    feature=feature,
                    index=index,
                    event_type="INVALIDATED",
                    reason_code="EXTERNAL_LIQUIDITY_CONSUMED_BEFORE_ENTRY",
                )
                continue

            if setup.phase == "WAIT_MSS":
                if self._failed_boundary_lost(setup, feature):
                    self.counts["failed_boundary_reaccepted_before_mss"] += 1
                    self._transition(
                        setup=setup,
                        feature=feature,
                        index=index,
                        event_type="INVALIDATED",
                        reason_code="FAILED_SWEEP_BOUNDARY_REACCEPTED_BEFORE_MSS",
                    )
                    continue
                if self._mss_confirmed(setup, feature):
                    setup.phase = "WAIT_RETEST"
                    setup.mss_index = index
                    setup.mss_time_ns = int(feature.bar.end_time_ns)
                    setup.mss_close = float(feature.bar.close)
                    # Do not use the MSS bar's unknown intrabar path for local
                    # invalidation.  The retest path begins on the next completed
                    # equal-notional event.
                    setup.retest_path_high = None
                    setup.retest_path_low = None
                    self.counts["mss_confirmed"] += 1
                    self._transition(
                        setup=setup,
                        feature=feature,
                        index=index,
                        event_type="STATE_CHANGED",
                        reason_code="NEAREST_OPPOSING_PIVOT_BROKEN_WITH_ALIGNED_FLOW",
                    )
                remaining.append(setup)
                continue

            # WAIT_RETEST: same-bar MSS/retest is deliberately impossible.
            if setup.mss_index is not None and index <= setup.mss_index:
                remaining.append(setup)
                continue
            setup.retest_path_high = (
                float(feature.bar.high)
                if setup.retest_path_high is None
                else max(setup.retest_path_high, float(feature.bar.high))
            )
            setup.retest_path_low = (
                float(feature.bar.low)
                if setup.retest_path_low is None
                else min(setup.retest_path_low, float(feature.bar.low))
            )

            if self._broken_pivot_retest(setup, feature):
                plan = self._plan(setup, feature, index)
                self.plans.append(plan)
                emitted.append(plan)
                self.counts["broken_pivot_retest_confirmed"] += 1
                self._transition(
                    setup=setup,
                    feature=feature,
                    index=index,
                    event_type="PLAN_EMITTED",
                    reason_code=plan.reason_code,
                )
                continue
            if self._broken_pivot_reaccepted(setup, feature):
                self.counts["broken_pivot_reaccepted"] += 1
                self._transition(
                    setup=setup,
                    feature=feature,
                    index=index,
                    event_type="INVALIDATED",
                    reason_code="BROKEN_INTERNAL_PIVOT_REACCEPTED",
                )
                continue
            remaining.append(setup)
        self.active = remaining
        return emitted

    def _apply_directional_change_invalidation(
        self,
        *,
        event: DirectionalChangeEvent,
        feature: EventFeature,
        index: int,
    ) -> None:
        remaining: list[MssRetestSetup] = []
        for setup in self.active:
            if self._contrary_event(setup.side, event):
                self.counts["contrary_directional_change"] += 1
                self._transition(
                    setup=setup,
                    feature=feature,
                    index=index,
                    event_type="INVALIDATED",
                    reason_code="CONTRARY_DIRECTIONAL_CHANGE_BEFORE_ENTRY",
                )
            else:
                remaining.append(setup)
        self.active = remaining

    def on_feature(
        self,
        *,
        index: int,
        features: list[EventFeature],
    ) -> list[ScenarioPlan]:
        feature = features[index]
        emitted = self._manage_active(index=index, feature=feature)

        event = self.detector.on_feature(index=index, features=features)
        if event is not None:
            self.counts[f"directional_change_{event.event_type.lower()}"] += 1
            self._apply_directional_change_invalidation(
                event=event,
                feature=feature,
                index=index,
            )
            self._arm_from_event(event=event, feature=feature)
        return emitted
