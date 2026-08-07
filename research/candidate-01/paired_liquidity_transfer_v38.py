#!/usr/bin/env python3
"""Paired external-liquidity transfer after a saturated intrinsic sweep.

The candidate is a strict causal state sequence:

1. a 40-bps intrinsic directional-change event sweeps a prior same-side pivot,
   closes back inside it, and reverses aggregate-trade flow;
2. the terminal outward event carries at least as much aggressive effort as the
   two preceding equal-notional events but produces less incremental extension
   (effort without result / local liquidity replenishment);
3. a completed opposite-flow event closes through the nearest opposing pivot
   and leaves a three-event fair-value gap while its true range is at least the
   causal median of the preceding twenty events;
4. the target is the nearest still-active completed-day/week liquidity level
   selected before the MSS, beyond the local opposing hierarchy;
5. the primary entry rests at the consequent-encroachment midpoint of the FVG;
   the control enters on the first later venue trade from the same plan;
6. invalidation is beyond the complete failed-sweep-to-MSS path plus one
   unchanged side-cost buffer.

The module contains signal logic only. NautilusTrader owns order matching,
fees, margin, positions, PnL and NAV.
"""
from __future__ import annotations

from dataclasses import dataclass
from statistics import median
from typing import Sequence

from calendar_target_mss_retest_v22 import CalendarTargetMssRetestStateMachine
from core import Side
from directional_change_failed_sweep_week import (
    STOP_BUFFER_FRACTION,
    DirectionalChangeEvent,
)
from directional_change_mss_retest_v21 import MssRetestSetup
from impact_regime_probe import EventFeature, ScenarioPlan
from nautilus_tick_limit_plan_backtest import RestingEntryInstruction

SATURATION_EVENTS = 3
DISPLACEMENT_LOOKBACK = 20
FVG_RESPONSE_WINDOW_NS = 30 * 60 * 1_000_000_000


@dataclass(frozen=True, slots=True)
class SaturationEvidence:
    confirmation_index: int
    side: str
    prior_effort_median: float
    terminal_effort: float
    prior_progress_median: float
    terminal_progress: float


@dataclass(frozen=True, slots=True)
class TransferDiagnostic:
    scenario_id: str
    signal_time_ns: int
    side: str
    failed_sweep_boundary: float
    broken_internal_pivot: float
    calendar_target: float
    fvg_lower: float
    fvg_upper: float
    resting_entry: float
    structural_stop: float
    signal_close: float
    prior_true_range_median: float
    signal_true_range: float
    expiry_time_ns: int


class PairedLiquidityTransferStateMachine(CalendarTargetMssRetestStateMachine):
    """Route a saturated failed auction through MSS/FVG to external liquidity."""

    def __init__(self) -> None:
        super().__init__()
        self._features: Sequence[EventFeature] = ()
        self.saturation_evidence: list[SaturationEvidence] = []
        self.transfer_diagnostics: list[TransferDiagnostic] = []
        self.instructions: list[RestingEntryInstruction] = []

    @staticmethod
    def _is_candidate_failed_sweep(
        machine: "PairedLiquidityTransferStateMachine",
        event: DirectionalChangeEvent,
    ) -> bool:
        if event.event_type == "DOWN":
            if not machine.high_events or len(machine.low_events) < 2:
                return False
            prior_same = machine.high_events[-1]
            opposing = machine.low_events[-2:]
            boundary = float(prior_same.pivot_price)
            internal = max(float(item.pivot_price) for item in opposing)
            intermediate = min(float(item.pivot_price) for item in opposing)
            return (
                event.pivot_price > boundary
                and event.confirmation_price < boundary
                and event.trend_flow_imbalance > 0.0
                and event.reversal_flow_imbalance < 0.0
                and intermediate < internal < boundary
            )
        if not machine.low_events or len(machine.high_events) < 2:
            return False
        prior_same = machine.low_events[-1]
        opposing = machine.high_events[-2:]
        boundary = float(prior_same.pivot_price)
        internal = min(float(item.pivot_price) for item in opposing)
        intermediate = max(float(item.pivot_price) for item in opposing)
        return (
            event.pivot_price < boundary
            and event.confirmation_price > boundary
            and event.trend_flow_imbalance < 0.0
            and event.reversal_flow_imbalance > 0.0
            and boundary < internal < intermediate
        )

    @staticmethod
    def _terminal_effort_without_result(
        event: DirectionalChangeEvent,
        features: Sequence[EventFeature],
    ) -> SaturationEvidence | None:
        pivot = int(event.pivot_index)
        start = pivot - SATURATION_EVENTS + 1
        if start <= 0 or start < int(event.trend_start_index):
            return None
        window = features[start : pivot + 1]
        if len(window) != SATURATION_EVENTS:
            return None

        outward_sign = 1.0 if event.event_type == "DOWN" else -1.0
        preceding = features[start - 1].bar
        running_extreme = preceding.high if outward_sign > 0.0 else preceding.low
        efforts: list[float] = []
        progresses: list[float] = []
        for item in window:
            bar = item.bar
            if bar.quote_notional <= 0.0 or running_extreme <= 0.0:
                return None
            efforts.append(
                outward_sign * bar.signed_quote_notional / bar.quote_notional,
            )
            if outward_sign > 0.0:
                extension = max(float(bar.high) - running_extreme, 0.0)
                progresses.append(extension / running_extreme)
                running_extreme = max(running_extreme, float(bar.high))
            else:
                extension = max(running_extreme - float(bar.low), 0.0)
                progresses.append(extension / running_extreme)
                running_extreme = min(running_extreme, float(bar.low))

        prior_effort = median(efforts[:-1])
        prior_progress = median(progresses[:-1])
        terminal_effort = efforts[-1]
        terminal_progress = progresses[-1]
        if (
            min(efforts) <= 0.0
            or prior_effort <= 0.0
            or prior_progress <= 0.0
            or terminal_effort < prior_effort
            or terminal_progress >= prior_progress
        ):
            return None
        side = Side.SHORT if event.event_type == "DOWN" else Side.LONG
        return SaturationEvidence(
            confirmation_index=int(event.confirmation_index),
            side=side.value,
            prior_effort_median=float(prior_effort),
            terminal_effort=float(terminal_effort),
            prior_progress_median=float(prior_progress),
            terminal_progress=float(terminal_progress),
        )

    def _arm_from_event(
        self,
        *,
        event: DirectionalChangeEvent,
        feature: EventFeature,
    ) -> None:
        if self._is_candidate_failed_sweep(self, event):
            evidence = self._terminal_effort_without_result(event, self._features)
            if evidence is None:
                # The directional-change event must still enter causal pivot
                # history even when it does not arm a trade scenario.
                if event.event_type == "DOWN":
                    self.high_events.append(event)
                else:
                    self.low_events.append(event)
                self.counts["failed_sweep_without_terminal_saturation"] += 1
                return
            self.saturation_evidence.append(evidence)
            self.counts["terminal_effort_without_result"] += 1
        super()._arm_from_event(event=event, feature=feature)

    @staticmethod
    def _fvg(
        features: Sequence[EventFeature],
        *,
        index: int,
        side: Side,
    ) -> tuple[float, float] | None:
        if index < 2:
            return None
        left = features[index - 2].bar
        current = features[index].bar
        if side is Side.LONG and float(current.low) > float(left.high):
            return float(left.high), float(current.low)
        if side is Side.SHORT and float(current.high) < float(left.low):
            return float(current.high), float(left.low)
        return None

    @staticmethod
    def _range_expanded(
        features: Sequence[EventFeature],
        *,
        index: int,
    ) -> tuple[bool, float]:
        start = index - DISPLACEMENT_LOOKBACK
        if start < 0:
            return False, 0.0
        history = [
            float(item.true_range)
            for item in features[start:index]
            if float(item.true_range) > 0.0
        ]
        if len(history) != DISPLACEMENT_LOOKBACK:
            return False, 0.0
        baseline = float(median(history))
        return float(features[index].true_range) >= baseline, baseline

    @staticmethod
    def _plan(
        *,
        setup: MssRetestSetup,
        feature: EventFeature,
        index: int,
        fvg_lower: float,
        fvg_upper: float,
    ) -> ScenarioPlan:
        stop = (
            float(setup.sweep_path_low) * (1.0 - STOP_BUFFER_FRACTION)
            if setup.side is Side.LONG
            else float(setup.sweep_path_high) * (1.0 + STOP_BUFFER_FRACTION)
        )
        return ScenarioPlan(
            scenario_id=setup.scenario_id + f":saturated-mss-fvg:{index}",
            response="EXHAUSTION_REVERSAL",
            side=setup.side,
            signal_bar_index=index,
            signal_time_ns=int(feature.bar.end_time_ns),
            stop_price=stop,
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
            pulse_high=float(feature.bar.high),
            pulse_low=float(feature.bar.low),
            pulse_flow_score=float(feature.imbalance_z or 0.0),
            pulse_move_atr=(
                float(feature.true_range) / float(feature.atr)
                if feature.atr is not None and float(feature.atr) > 0.0
                else 0.0
            ),
            pulse_path_efficiency=1.0,
            pulse_close_location=float(feature.bar.close_location),
            reason_code=(
                "SATURATED_FAILED_SWEEP_MSS_FVG_TO_CALENDAR_EXTERNAL_LIQUIDITY"
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

            setup.sweep_path_high = max(
                float(setup.sweep_path_high),
                float(feature.bar.high),
            )
            setup.sweep_path_low = min(
                float(setup.sweep_path_low),
                float(feature.bar.low),
            )
            if self._target_touched(setup, feature):
                self.counts["target_consumed_before_entry"] += 1
                continue
            if self._failed_boundary_lost(setup, feature):
                self.counts["failed_boundary_reaccepted_before_mss"] += 1
                continue
            if not self._mss_confirmed(setup, feature):
                remaining.append(setup)
                continue

            fvg = self._fvg(self._features, index=index, side=setup.side)
            expanded, baseline = self._range_expanded(self._features, index=index)
            if fvg is None or not expanded:
                remaining.append(setup)
                continue
            fvg_lower, fvg_upper = fvg
            entry = 0.5 * (fvg_lower + fvg_upper)
            signal_close = float(feature.bar.close)
            passive = (
                entry < signal_close
                if setup.side is Side.LONG
                else entry > signal_close
            )
            if not passive:
                remaining.append(setup)
                continue

            plan = self._plan(
                setup=setup,
                feature=feature,
                index=index,
                fvg_lower=fvg_lower,
                fvg_upper=fvg_upper,
            )
            expiry = int(plan.signal_time_ns) + FVG_RESPONSE_WINDOW_NS
            instruction = RestingEntryInstruction(
                plan=plan,
                entry_price=float(entry),
                expiry_time_ns=expiry,
                entry_reason="FIRST_MSS_FVG_CONSEQUENT_ENCROACHMENT",
            )
            self.plans.append(plan)
            self.instructions.append(instruction)
            emitted.append(plan)
            self.counts["saturated_mss_fvg_plan_emitted"] += 1
            self.transfer_diagnostics.append(
                TransferDiagnostic(
                    scenario_id=plan.scenario_id,
                    signal_time_ns=int(plan.signal_time_ns),
                    side=plan.side.value,
                    failed_sweep_boundary=float(setup.boundary),
                    broken_internal_pivot=float(setup.internal_pivot),
                    calendar_target=float(setup.external_target),
                    fvg_lower=float(fvg_lower),
                    fvg_upper=float(fvg_upper),
                    resting_entry=float(entry),
                    structural_stop=float(plan.stop_price),
                    signal_close=signal_close,
                    prior_true_range_median=baseline,
                    signal_true_range=float(feature.true_range),
                    expiry_time_ns=expiry,
                ),
            )
        self.active = remaining
        return emitted

    def on_feature(
        self,
        *,
        index: int,
        features: list[EventFeature],
    ) -> list[ScenarioPlan]:
        self._features = features
        return super().on_feature(index=index, features=features)


__all__ = [
    "DISPLACEMENT_LOOKBACK",
    "FVG_RESPONSE_WINDOW_NS",
    "PairedLiquidityTransferStateMachine",
    "SATURATION_EVENTS",
    "SaturationEvidence",
    "TransferDiagnostic",
]
