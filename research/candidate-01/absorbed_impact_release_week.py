#!/usr/bin/env python3
"""Causal absorbed-impact release candidate on the first BTC week.

This scenario addresses the structural failure of chasing already completed
initiative moves.  It separates the pulse detector from the trading response:

1. A completed three-event aggressive-flow pulse probes external liquidity by
   at least 0.25 event ATR.
2. Despite that effort, the close retains at most 35% of the outward excursion
   and finishes in the adverse 45% of the pulse range.  This is interpreted as
   aggressive flow absorbed by passive inventory, not as durable outside value.
3. No trade is entered from the pulse itself.
4. Within three completed events, opposite flow z >= 0.50, price is back inside
   the broken boundary, and the pulse midpoint is crossed.
5. Enter at the next event open only if the failed boundary still holds.
6. Invalidate beyond every observed pulse/confirmation-path extreme plus 0.15
   ATR; target the opposite edge of the pre-impact structure.

The event clock is fixed by the intraday holding contract: each UTC day uses the
immediately preceding completed day's ten-minute-equivalent quote notional.
No clock or scenario parameter is selected from strategy PnL.  Execution uses
7 bps per side, current-NAV 3% planned risk, stop-first ambiguity and one global
position.  One invocation evaluates exactly one BTC week.
"""

from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import timedelta
import json
from pathlib import Path
import sys
from typing import Any

import pandas as pd

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
SRC = ROOT / "src"
for item in (HERE, SRC):
    if str(item) not in sys.path:
        sys.path.insert(0, str(item))

from adaptive_aggtrade_clock import build_daily_cost_resolved_bars  # noqa: E402
from aggtrade_data import download_aggtrade_days  # noqa: E402
from core import Side  # noqa: E402
from data import parse_utc_date  # noqa: E402
from impact_regime_probe import EventFeature, ImpactRegimeDetector, PulseEvent, ScenarioPlan, simulate  # noqa: E402


CLOCK_MINUTES = (10,)
CLOCK_RANGE_FLOOR_BPS = 1e-6
MIN_OUTWARD_EXCURSION_ATR = 0.25
MAX_OUTWARD_RETENTION = 0.35
MAX_ALIGNED_CLOSE_LOCATION = 0.45
MIN_STRUCTURE_WIDTH_ATR = 1.25
CONFIRMATION_WINDOW_BARS = 3
OPPOSITE_FLOW_CONFIRM_Z = 0.50
INSIDE_DEPTH_ATR = 0.05
STOP_BUFFER_ATR = 0.15


@dataclass(slots=True)
class AbsorbedImpactSetup:
    scenario_id: str
    outward_side: Side
    reversal_side: Side
    created_index: int
    expiry_index: int
    atr: float
    boundary: float
    pulse_midpoint: float
    path_high: float
    path_low: float
    target_price: float
    structure_high: float
    structure_low: float
    structure_midpoint: float
    pulse_flow_score: float
    pulse_move_atr: float
    pulse_path_efficiency: float
    pulse_close_location: float


@dataclass(frozen=True, slots=True)
class AbsorptionDecision:
    scenario_id: str
    event_index: int
    event_time_ns: int
    direction: str
    accepted: bool
    reason_code: str
    outward_excursion_atr: float
    close_beyond_boundary_atr: float
    outward_retention: float | None
    aligned_close_location: float
    move_atr: float
    path_efficiency: float
    structure_width_atr: float


@dataclass(frozen=True, slots=True)
class AbsorptionTransition:
    scenario_id: str
    event_type: str
    event_index: int
    event_time_ns: int
    reason_code: str
    outward_side: str
    reversal_side: str
    boundary: float
    pulse_midpoint: float
    path_high: float
    path_low: float
    target_price: float
    opposite_flow_z: float | None
    close: float


def classify_absorption(pulse: PulseEvent) -> tuple[bool, str, float | None]:
    retention = (
        pulse.close_beyond_boundary_atr / pulse.outward_excursion_atr
        if pulse.outward_excursion_atr > 0.0
        else None
    )
    if pulse.classification == "EFFICIENT_CONTINUATION":
        return False, "DURABLE_OUTSIDE_VALUE", retention
    if pulse.structure_width_atr < MIN_STRUCTURE_WIDTH_ATR:
        return False, "STRUCTURE_TOO_NARROW", retention
    if pulse.outward_excursion_atr < MIN_OUTWARD_EXCURSION_ATR:
        return False, "NO_EXTERNAL_LIQUIDITY_PROBE", retention
    if retention is None or retention > MAX_OUTWARD_RETENTION:
        return False, "OUTWARD_MOVE_RETAINED", retention
    if pulse.aligned_close_location > MAX_ALIGNED_CLOSE_LOCATION:
        return False, "PULSE_CLOSE_NOT_REJECTED", retention
    return True, "ABSORBED_EXTERNAL_IMPACT", retention


class AbsorbedImpactStateMachine:
    def __init__(self) -> None:
        self.active: list[AbsorbedImpactSetup] = []
        self.plans: list[ScenarioPlan] = []
        self.decisions: list[AbsorptionDecision] = []
        self.transitions: list[AbsorptionTransition] = []
        self.counts: Counter[str] = Counter()

    @staticmethod
    def _reversal_side(side: Side) -> Side:
        return Side.SHORT if side is Side.LONG else Side.LONG

    def _transition(
        self,
        *,
        setup: AbsorbedImpactSetup,
        feature: EventFeature,
        index: int,
        event_type: str,
        reason_code: str,
        opposite_flow_z: float | None,
    ) -> None:
        self.transitions.append(
            AbsorptionTransition(
                scenario_id=setup.scenario_id,
                event_type=event_type,
                event_index=index,
                event_time_ns=feature.bar.end_time_ns,
                reason_code=reason_code,
                outward_side=setup.outward_side.value,
                reversal_side=setup.reversal_side.value,
                boundary=setup.boundary,
                pulse_midpoint=setup.pulse_midpoint,
                path_high=setup.path_high,
                path_low=setup.path_low,
                target_price=setup.target_price,
                opposite_flow_z=opposite_flow_z,
                close=feature.bar.close,
            ),
        )

    def observe_pulse(self, *, pulse: PulseEvent, feature: EventFeature) -> None:
        accepted, reason, retention = classify_absorption(pulse)
        self.decisions.append(
            AbsorptionDecision(
                scenario_id=pulse.scenario_id,
                event_index=pulse.bar_index,
                event_time_ns=pulse.event_time_ns,
                direction=pulse.direction,
                accepted=accepted,
                reason_code=reason,
                outward_excursion_atr=pulse.outward_excursion_atr,
                close_beyond_boundary_atr=pulse.close_beyond_boundary_atr,
                outward_retention=retention,
                aligned_close_location=pulse.aligned_close_location,
                move_atr=pulse.move_atr,
                path_efficiency=pulse.path_efficiency,
                structure_width_atr=pulse.structure_width_atr,
            ),
        )
        self.counts[reason] += 1
        if not accepted:
            return
        side = Side(pulse.direction)
        reversal = self._reversal_side(side)
        boundary = pulse.structure_high if side is Side.LONG else pulse.structure_low
        setup = AbsorbedImpactSetup(
            scenario_id=pulse.scenario_id + ":absorbed-impact",
            outward_side=side,
            reversal_side=reversal,
            created_index=pulse.bar_index,
            expiry_index=pulse.bar_index + CONFIRMATION_WINDOW_BARS,
            atr=pulse.atr,
            boundary=boundary,
            pulse_midpoint=0.5 * (pulse.pulse_high + pulse.pulse_low),
            path_high=pulse.pulse_high,
            path_low=pulse.pulse_low,
            target_price=(pulse.structure_low if reversal is Side.SHORT else pulse.structure_high),
            structure_high=pulse.structure_high,
            structure_low=pulse.structure_low,
            structure_midpoint=0.5 * (pulse.structure_high + pulse.structure_low),
            pulse_flow_score=pulse.flow_score,
            pulse_move_atr=pulse.move_atr,
            pulse_path_efficiency=pulse.path_efficiency,
            pulse_close_location=pulse.aligned_close_location,
        )
        self.active.append(setup)
        self.counts["armed"] += 1
        self._transition(
            setup=setup,
            feature=feature,
            index=pulse.bar_index,
            event_type="ARMED",
            reason_code="AGGRESSIVE_IMPACT_ABSORBED",
            opposite_flow_z=None,
        )

    @staticmethod
    def _target_touched(setup: AbsorbedImpactSetup, feature: EventFeature) -> bool:
        return (
            feature.bar.low <= setup.target_price
            if setup.reversal_side is Side.SHORT
            else feature.bar.high >= setup.target_price
        )

    @staticmethod
    def _confirmed(
        setup: AbsorbedImpactSetup,
        feature: EventFeature,
    ) -> tuple[bool, float | None]:
        z = feature.imbalance_z
        opposite_z = setup.reversal_side.sign * z if z is not None else None
        if setup.outward_side is Side.LONG:
            inside = feature.bar.close <= setup.boundary - INSIDE_DEPTH_ATR * setup.atr
            midpoint_break = feature.bar.close < setup.pulse_midpoint
        else:
            inside = feature.bar.close >= setup.boundary + INSIDE_DEPTH_ATR * setup.atr
            midpoint_break = feature.bar.close > setup.pulse_midpoint
        return (
            opposite_z is not None
            and opposite_z >= OPPOSITE_FLOW_CONFIRM_Z
            and inside
            and midpoint_break
        ), opposite_z

    @staticmethod
    def _build_plan(
        setup: AbsorbedImpactSetup,
        feature: EventFeature,
        index: int,
    ) -> ScenarioPlan:
        stop = (
            setup.path_high + STOP_BUFFER_ATR * setup.atr
            if setup.reversal_side is Side.SHORT
            else setup.path_low - STOP_BUFFER_ATR * setup.atr
        )
        return ScenarioPlan(
            scenario_id=setup.scenario_id + f":release:{index}",
            response="EXHAUSTION_REVERSAL",
            side=setup.reversal_side,
            signal_bar_index=index,
            signal_time_ns=feature.bar.end_time_ns,
            stop_price=stop,
            target_price=setup.target_price,
            confirmation_hold_price=setup.boundary,
            structure_high=setup.structure_high,
            structure_low=setup.structure_low,
            structure_midpoint=setup.structure_midpoint,
            pulse_high=setup.path_high,
            pulse_low=setup.path_low,
            pulse_flow_score=setup.pulse_flow_score,
            pulse_move_atr=setup.pulse_move_atr,
            pulse_path_efficiency=setup.pulse_path_efficiency,
            pulse_close_location=setup.pulse_close_location,
            reason_code="ABSORBED_IMPACT_OPPOSITE_RELEASE_CONFIRMED",
        )

    def on_feature(self, *, index: int, feature: EventFeature) -> list[ScenarioPlan]:
        emitted: list[ScenarioPlan] = []
        remaining: list[AbsorbedImpactSetup] = []
        for setup in self.active:
            if index <= setup.created_index:
                remaining.append(setup)
                continue
            if index > setup.expiry_index:
                self.counts["expired"] += 1
                self._transition(
                    setup=setup,
                    feature=feature,
                    index=index,
                    event_type="INVALIDATED",
                    reason_code="RELEASE_CONFIRMATION_EXPIRED",
                    opposite_flow_z=None,
                )
                continue
            setup.path_high = max(setup.path_high, feature.bar.high)
            setup.path_low = min(setup.path_low, feature.bar.low)
            if self._target_touched(setup, feature):
                self.counts["target_consumed"] += 1
                self._transition(
                    setup=setup,
                    feature=feature,
                    index=index,
                    event_type="INVALIDATED",
                    reason_code="OPPOSITE_LIQUIDITY_ALREADY_CONSUMED",
                    opposite_flow_z=None,
                )
                continue
            confirmed, opposite_z = self._confirmed(setup, feature)
            if confirmed:
                plan = self._build_plan(setup, feature, index)
                self.plans.append(plan)
                emitted.append(plan)
                self.counts["confirmed"] += 1
                self._transition(
                    setup=setup,
                    feature=feature,
                    index=index,
                    event_type="PLAN_EMITTED",
                    reason_code=plan.reason_code,
                    opposite_flow_z=opposite_z,
                )
                continue
            if index == setup.expiry_index:
                self.counts["expired"] += 1
                self._transition(
                    setup=setup,
                    feature=feature,
                    index=index,
                    event_type="INVALIDATED",
                    reason_code="RELEASE_CONFIRMATION_EXPIRED",
                    opposite_flow_z=opposite_z,
                )
                continue
            remaining.append(setup)
        self.active = remaining
        return emitted


def atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)


def run(args: argparse.Namespace) -> int:
    raw = json.loads(args.config.read_text(encoding="utf-8"))
    execution = dict(raw["execution"])
    evaluation_start = parse_utc_date(args.week)
    evaluation_end = evaluation_start + timedelta(days=7)
    feature_warmup_start = evaluation_start - timedelta(days=1)
    clock_source_start = evaluation_start - timedelta(days=2)
    start_ns = int(pd.Timestamp(evaluation_start).as_unit("ns").value)
    end_ns = int(pd.Timestamp(evaluation_end).as_unit("ns").value)

    records = download_aggtrade_days(
        symbol="BTCUSDT",
        start=clock_source_start,
        end=evaluation_end,
        cache_dir=args.cache,
        workers=args.workers,
    )
    bars, calibrations = build_daily_cost_resolved_bars(
        records,
        bar_start=feature_warmup_start,
        bar_end=evaluation_end,
        minimum_range_bps=CLOCK_RANGE_FLOOR_BPS,
        candidate_minutes=CLOCK_MINUTES,
    )

    detector = ImpactRegimeDetector()
    scenario = AbsorbedImpactStateMachine()
    previous_pulses = 0
    for index, bar in enumerate(bars):
        detector.on_bar(bar)
        feature = detector.features[-1]
        scenario.on_feature(index=index, feature=feature)
        for pulse in detector.pulse_events[previous_pulses:]:
            scenario.observe_pulse(pulse=pulse, feature=feature)
        previous_pulses = len(detector.pulse_events)

    trades, metrics, daily, rejections = simulate(
        features=detector.features,
        plans=scenario.plans,
        evaluation_start_ns=start_ns,
        evaluation_end_ns=end_ns,
        starting_nav=float(execution["starting_nav"]),
        cost=float(execution["all_in_cost_bps_per_side"]) / 10_000.0,
        exit_on_boundary_reacceptance=False,
    )
    evaluation_bars = [bar for bar in bars if start_ns <= bar.end_time_ns < end_ns]
    evaluation_calibrations = [
        item
        for item in calibrations
        if evaluation_start.date().isoformat() <= item.bar_day < evaluation_end.date().isoformat()
    ]

    output = args.output
    output.mkdir(parents=True, exist_ok=True)
    trades.to_csv(output / "trades.csv", index=False)
    daily.to_csv(output / "daily_nav.csv", index=False)
    rejections.to_csv(output / "rejections.csv", index=False)
    pd.DataFrame(asdict(row) for row in scenario.decisions).to_csv(
        output / "absorption_decisions.csv",
        index=False,
    )
    pd.DataFrame(asdict(row) for row in scenario.transitions).to_csv(
        output / "scenario_transitions.csv",
        index=False,
    )
    pd.DataFrame(item.to_dict() for item in calibrations).to_json(
        output / "daily_clock_calibrations.json",
        orient="records",
        indent=2,
    )
    payload = {
        "candidate": "absorbed external impact opposite release",
        "evaluation_start_utc": evaluation_start.isoformat(),
        "evaluation_end_utc": evaluation_end.isoformat(),
        "clock_minutes": list(CLOCK_MINUTES),
        "evaluation_selected_minutes": [item.selected_minutes for item in evaluation_calibrations],
        "evaluation_event_bars": len(evaluation_bars),
        "event_bars_per_day": len(evaluation_bars) / 7.0,
        "scenario_parameters": {
            "minimum_outward_excursion_atr": MIN_OUTWARD_EXCURSION_ATR,
            "maximum_outward_retention": MAX_OUTWARD_RETENTION,
            "maximum_aligned_close_location": MAX_ALIGNED_CLOSE_LOCATION,
            "minimum_structure_width_atr": MIN_STRUCTURE_WIDTH_ATR,
            "confirmation_window_bars": CONFIRMATION_WINDOW_BARS,
            "opposite_flow_confirm_z": OPPOSITE_FLOW_CONFIRM_Z,
            "inside_depth_atr": INSIDE_DEPTH_ATR,
            "stop_buffer_atr": STOP_BUFFER_ATR,
        },
        "detector_counts": dict(detector.counts),
        "scenario_counts": dict(scenario.counts),
        "metrics": metrics,
        "downloads": [record.to_dict() for record in records],
        "long_evaluation_run": False,
    }
    atomic_json(output / "absorbed_impact_release_week_summary.json", payload)
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--week", required=True)
    parser.add_argument("--config", type=Path, default=HERE / "config.json")
    parser.add_argument("--cache", type=Path, default=ROOT / ".cache" / "candidate-01-impact-resolution-adaptive-first")
    parser.add_argument("--output", type=Path, default=ROOT / "artifacts" / "candidate-01-absorbed-impact-release")
    parser.add_argument("--workers", type=int, default=4)
    return parser


if __name__ == "__main__":
    raise SystemExit(run(build_parser().parse_args()))
