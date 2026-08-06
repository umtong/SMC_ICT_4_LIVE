#!/usr/bin/env python3
"""Calendar-target failed sweep entered on completed MSS displacement.

The v22 failed-sweep detector, causal calendar target book and target selection
are frozen. The sole candidate variable is the entry/invalidation stage.

Instead of waiting for a broken-pivot retest whose local stop is smaller than
the 14-bps round-trip cost, a plan is emitted when a completed equal-notional
event first closes through the nearest opposing pivot with aligned aggressive
flow. Entry remains the first later venue TradeTick. Invalidation is beyond the
adverse extreme of every completed failed-sweep-to-MSS event plus the unchanged
7-bps side-cost buffer. The selected calendar target must remain untouched.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass

from core import Response, Side
from directional_change_failed_sweep_week import STOP_BUFFER_FRACTION
from directional_change_mss_retest_v21 import MssRetestSetup
from impact_regime_probe import EventFeature, ScenarioPlan
from calendar_target_mss_retest_v22 import CalendarTargetMssRetestStateMachine


@dataclass(frozen=True, slots=True)
class MssDisplacementDiagnostic:
    scenario_id: str
    signal_time_ns: int
    side: str
    failed_sweep_boundary: float
    broken_internal_pivot: float
    calendar_target: float
    mss_close: float
    path_high: float
    path_low: float
    structural_stop: float
    price_risk_bps_at_mss_close: float
    gross_reward_bps_at_mss_close: float


class CalendarMssDisplacementStateMachine(
    CalendarTargetMssRetestStateMachine,
):
    """Emit at causal MSS close with full failed-sweep-to-MSS invalidation."""

    def __init__(self) -> None:
        super().__init__()
        self.displacement_diagnostics: list[MssDisplacementDiagnostic] = []

    @staticmethod
    def _displacement_plan(
        setup: MssRetestSetup,
        feature: EventFeature,
        index: int,
    ) -> ScenarioPlan:
        stop = (
            setup.sweep_path_low * (1.0 - STOP_BUFFER_FRACTION)
            if setup.side is Side.LONG
            else setup.sweep_path_high * (1.0 + STOP_BUFFER_FRACTION)
        )
        return ScenarioPlan(
            scenario_id=setup.scenario_id + f":mss-displacement:{index}",
            response=Response.SWEEP_FAILURE.value,
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
            pulse_high=float(setup.sweep_path_high),
            pulse_low=float(setup.sweep_path_low),
            pulse_flow_score=float(setup.reversal_flow_imbalance),
            pulse_move_atr=0.0,
            pulse_path_efficiency=0.0,
            pulse_close_location=0.0,
            reason_code=(
                "FAILED_SWEEP_MSS_DISPLACEMENT_TO_"
                "CALENDAR_EXTERNAL_LIQUIDITY"
            ),
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
            if setup.phase != "WAIT_MSS":
                raise RuntimeError(
                    f"v23 received unexpected phase {setup.phase}: {setup.scenario_id}",
                )

            # Every completed event observed before confirmation belongs to the
            # causal invalidation path. No future event is used.
            setup.sweep_path_high = max(
                float(setup.sweep_path_high),
                float(feature.bar.high),
            )
            setup.sweep_path_low = min(
                float(setup.sweep_path_low),
                float(feature.bar.low),
            )

            if self._target_touched(setup, feature):
                self.counts["target_consumed_before_mss_entry"] += 1
                self._transition(
                    setup=setup,
                    feature=feature,
                    index=index,
                    event_type="INVALIDATED",
                    reason_code="CALENDAR_EXTERNAL_LIQUIDITY_CONSUMED_BEFORE_MSS",
                )
                continue
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
            if not self._mss_confirmed(setup, feature):
                remaining.append(setup)
                continue

            plan = self._displacement_plan(setup, feature, index)
            self.plans.append(plan)
            emitted.append(plan)
            self.counts["mss_confirmed"] += 1
            self.counts["mss_displacement_plan_emitted"] += 1
            entry = float(feature.bar.close)
            price_risk = abs(entry - float(plan.stop_price))
            gross_reward = abs(float(plan.target_price) - entry)
            self.displacement_diagnostics.append(
                MssDisplacementDiagnostic(
                    scenario_id=plan.scenario_id,
                    signal_time_ns=int(plan.signal_time_ns),
                    side=plan.side.value,
                    failed_sweep_boundary=float(setup.boundary),
                    broken_internal_pivot=float(setup.internal_pivot),
                    calendar_target=float(setup.external_target),
                    mss_close=entry,
                    path_high=float(setup.sweep_path_high),
                    path_low=float(setup.sweep_path_low),
                    structural_stop=float(plan.stop_price),
                    price_risk_bps_at_mss_close=(
                        price_risk / entry * 10_000.0
                    ),
                    gross_reward_bps_at_mss_close=(
                        gross_reward / entry * 10_000.0
                    ),
                ),
            )
            self._transition(
                setup=setup,
                feature=feature,
                index=index,
                event_type="PLAN_EMITTED",
                reason_code=plan.reason_code,
            )
        self.active = remaining
        return emitted
