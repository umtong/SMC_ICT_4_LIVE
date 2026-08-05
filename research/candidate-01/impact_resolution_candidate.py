#!/usr/bin/env python3
"""Causal resolved-impact day-trading candidate for the first BTC week.

An efficient aggregate-trade initiative beyond completed external liquidity is
not itself an entry.  The market is given exactly three completed equal-
notional events to reveal which of two mutually exclusive auction outcomes
occurred:

* failed impact: outside value is lost, opposite flow appears, and price crosses
  the initiative midpoint -> trade the reversal to the opposite structure edge;
* durable impact: the full response window expires without failure, at least two
  response closes retain outside value, and cumulative aligned flow remains
  positive -> trade continuation to the measured external target.

Failure has precedence over continuation.  This repairs the earlier logical
error where an early continuation confirmation entered before the same
initiative completed a failed-auction reversal.  Stops include every observed
initiative/response-path extreme.  Targets already consumed before confirmation
are rejected.  Entry is the next event open with confirmation hold, 7 bps per
side, current-NAV 3% risk, stop-first bar ambiguity, and one global position.
Only the first frozen BTC week is evaluated here.
"""

from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import timedelta
import json
from pathlib import Path
import sys
from typing import Any, Iterable

import pandas as pd

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
SRC = ROOT / "src"
for item in (HERE, SRC):
    if str(item) not in sys.path:
        sys.path.insert(0, str(item))

from aggtrade_clock import calibrate_target_from_minutes, iter_volume_bars, minute_quote_totals  # noqa: E402
from aggtrade_data import download_aggtrade_days, iter_downloads  # noqa: E402
from core import Side  # noqa: E402
from data import parse_utc_date  # noqa: E402
from impact_regime_probe import (  # noqa: E402
    CLOCK_CALIBRATION_MINUTES,
    EventFeature,
    ImpactRegimeDetector,
    ScenarioPlan,
    simulate,
)


RESOLUTION_WINDOW_BARS = 3
OPPOSITE_FLOW_CONFIRM_Z = 0.50
CONTINUATION_FLOW_SUM_Z = 0.50
INSIDE_DEPTH_ATR = 0.05
OUTSIDE_DEPTH_ATR = 0.05
MIN_OUTSIDE_HOLDS = 2
STOP_BUFFER_ATR = 0.15


@dataclass(slots=True)
class ImpactResolutionSetup:
    initiative_plan: ScenarioPlan
    created_index: int
    expiry_index: int
    atr: float
    outward_side: Side
    reversal_side: Side
    boundary: float
    pulse_midpoint: float
    path_high: float
    path_low: float
    reversal_target: float
    continuation_target: float
    outside_hold_count: int = 0
    aligned_flow_sum_z: float = 0.0
    reversal_target_consumed: bool = False
    continuation_target_consumed: bool = False


@dataclass(frozen=True, slots=True)
class ImpactResolutionTransition:
    scenario_id: str
    event_type: str
    event_index: int
    event_time_ns: int
    reason_code: str
    outward_side: str
    selected_side: str | None
    boundary: float
    pulse_midpoint: float
    path_high: float
    path_low: float
    reversal_target: float
    continuation_target: float
    outside_hold_count: int
    aligned_flow_sum_z: float
    aligned_flow_z: float | None
    opposite_flow_z: float | None
    close: float


class ImpactResolutionStateMachine:
    """Resolve each efficient initiative once, with failure taking precedence."""

    def __init__(self) -> None:
        self.active: list[ImpactResolutionSetup] = []
        self.plans: list[ScenarioPlan] = []
        self.transitions: list[ImpactResolutionTransition] = []
        self.counts: Counter[str] = Counter()

    @staticmethod
    def _reversal_side(side: Side) -> Side:
        return Side.SHORT if side is Side.LONG else Side.LONG

    def _transition(
        self,
        *,
        setup: ImpactResolutionSetup,
        feature: EventFeature,
        index: int,
        event_type: str,
        reason_code: str,
        selected_side: Side | None,
        aligned_z: float | None,
        opposite_z: float | None,
    ) -> None:
        self.transitions.append(
            ImpactResolutionTransition(
                scenario_id=setup.initiative_plan.scenario_id,
                event_type=event_type,
                event_index=index,
                event_time_ns=feature.bar.end_time_ns,
                reason_code=reason_code,
                outward_side=setup.outward_side.value,
                selected_side=(selected_side.value if selected_side is not None else None),
                boundary=setup.boundary,
                pulse_midpoint=setup.pulse_midpoint,
                path_high=setup.path_high,
                path_low=setup.path_low,
                reversal_target=setup.reversal_target,
                continuation_target=setup.continuation_target,
                outside_hold_count=setup.outside_hold_count,
                aligned_flow_sum_z=setup.aligned_flow_sum_z,
                aligned_flow_z=aligned_z,
                opposite_flow_z=opposite_z,
                close=feature.bar.close,
            ),
        )

    def arm(self, plan: ScenarioPlan, *, atr: float, feature: EventFeature) -> None:
        if plan.response != "CONTINUATION":
            return
        if atr <= 0.0:
            raise ValueError("resolved impact requires positive ATR")
        reversal_side = self._reversal_side(plan.side)
        setup = ImpactResolutionSetup(
            initiative_plan=plan,
            created_index=plan.signal_bar_index,
            expiry_index=plan.signal_bar_index + RESOLUTION_WINDOW_BARS,
            atr=atr,
            outward_side=plan.side,
            reversal_side=reversal_side,
            boundary=plan.confirmation_hold_price,
            pulse_midpoint=0.5 * (plan.pulse_high + plan.pulse_low),
            path_high=plan.pulse_high,
            path_low=plan.pulse_low,
            reversal_target=(plan.structure_low if reversal_side is Side.SHORT else plan.structure_high),
            continuation_target=plan.target_price,
        )
        self.active.append(setup)
        self.counts["armed"] += 1
        self._transition(
            setup=setup,
            feature=feature,
            index=plan.signal_bar_index,
            event_type="ARMED",
            reason_code="EFFICIENT_OUTSIDE_INITIATIVE_OBSERVED",
            selected_side=None,
            aligned_z=None,
            opposite_z=None,
        )

    @staticmethod
    def _reversal_target_touched(setup: ImpactResolutionSetup, feature: EventFeature) -> bool:
        return (
            feature.bar.low <= setup.reversal_target
            if setup.reversal_side is Side.SHORT
            else feature.bar.high >= setup.reversal_target
        )

    @staticmethod
    def _continuation_target_touched(setup: ImpactResolutionSetup, feature: EventFeature) -> bool:
        return (
            feature.bar.high >= setup.continuation_target
            if setup.outward_side is Side.LONG
            else feature.bar.low <= setup.continuation_target
        )

    def _failed(self, setup: ImpactResolutionSetup, feature: EventFeature) -> tuple[bool, float | None]:
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

    def _outside(self, setup: ImpactResolutionSetup, feature: EventFeature) -> bool:
        return (
            feature.bar.close >= setup.boundary + OUTSIDE_DEPTH_ATR * setup.atr
            if setup.outward_side is Side.LONG
            else feature.bar.close <= setup.boundary - OUTSIDE_DEPTH_ATR * setup.atr
        )

    def _reversal_plan(self, setup: ImpactResolutionSetup, feature: EventFeature, index: int) -> ScenarioPlan:
        source = setup.initiative_plan
        stop = (
            setup.path_high + STOP_BUFFER_ATR * setup.atr
            if setup.reversal_side is Side.SHORT
            else setup.path_low - STOP_BUFFER_ATR * setup.atr
        )
        return ScenarioPlan(
            scenario_id=source.scenario_id + f":resolved-reversal:{index}",
            response="EXHAUSTION_REVERSAL",
            side=setup.reversal_side,
            signal_bar_index=index,
            signal_time_ns=feature.bar.end_time_ns,
            stop_price=stop,
            target_price=setup.reversal_target,
            confirmation_hold_price=setup.boundary,
            structure_high=source.structure_high,
            structure_low=source.structure_low,
            structure_midpoint=source.structure_midpoint,
            pulse_high=setup.path_high,
            pulse_low=setup.path_low,
            pulse_flow_score=source.pulse_flow_score,
            pulse_move_atr=source.pulse_move_atr,
            pulse_path_efficiency=source.pulse_path_efficiency,
            pulse_close_location=source.pulse_close_location,
            reason_code="OUTSIDE_IMPACT_FAILED_AND_REVERSED",
        )

    def _continuation_plan(self, setup: ImpactResolutionSetup, feature: EventFeature, index: int) -> ScenarioPlan:
        source = setup.initiative_plan
        stop = (
            setup.path_low - STOP_BUFFER_ATR * setup.atr
            if setup.outward_side is Side.LONG
            else setup.path_high + STOP_BUFFER_ATR * setup.atr
        )
        return ScenarioPlan(
            scenario_id=source.scenario_id + f":resolved-continuation:{index}",
            response="CONTINUATION",
            side=setup.outward_side,
            signal_bar_index=index,
            signal_time_ns=feature.bar.end_time_ns,
            stop_price=stop,
            target_price=setup.continuation_target,
            confirmation_hold_price=setup.boundary,
            structure_high=source.structure_high,
            structure_low=source.structure_low,
            structure_midpoint=source.structure_midpoint,
            pulse_high=setup.path_high,
            pulse_low=setup.path_low,
            pulse_flow_score=source.pulse_flow_score,
            pulse_move_atr=source.pulse_move_atr,
            pulse_path_efficiency=source.pulse_path_efficiency,
            pulse_close_location=source.pulse_close_location,
            reason_code="OUTSIDE_IMPACT_DURABLY_ACCEPTED",
        )

    def on_feature(
        self,
        *,
        index: int,
        feature: EventFeature,
        new_initiative_plans: Iterable[ScenarioPlan] = (),
    ) -> list[ScenarioPlan]:
        emitted: list[ScenarioPlan] = []
        remaining: list[ImpactResolutionSetup] = []
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
                    reason_code="RESOLUTION_WINDOW_EXPIRED",
                    selected_side=None,
                    aligned_z=None,
                    opposite_z=None,
                )
                continue

            setup.path_high = max(setup.path_high, feature.bar.high)
            setup.path_low = min(setup.path_low, feature.bar.low)
            aligned_z = (
                setup.outward_side.sign * feature.imbalance_z
                if feature.imbalance_z is not None
                else None
            )
            if aligned_z is not None:
                setup.aligned_flow_sum_z += aligned_z
            if self._outside(setup, feature):
                setup.outside_hold_count += 1
            if self._reversal_target_touched(setup, feature):
                setup.reversal_target_consumed = True
            if self._continuation_target_touched(setup, feature):
                setup.continuation_target_consumed = True

            failed, opposite_z = self._failed(setup, feature)
            if failed:
                if setup.reversal_target_consumed:
                    self.counts["reversal_target_consumed"] += 1
                    self._transition(
                        setup=setup,
                        feature=feature,
                        index=index,
                        event_type="INVALIDATED",
                        reason_code="REVERSAL_TARGET_ALREADY_CONSUMED",
                        selected_side=None,
                        aligned_z=aligned_z,
                        opposite_z=opposite_z,
                    )
                else:
                    plan = self._reversal_plan(setup, feature, index)
                    self.plans.append(plan)
                    emitted.append(plan)
                    self.counts["resolved_reversal"] += 1
                    self._transition(
                        setup=setup,
                        feature=feature,
                        index=index,
                        event_type="PLAN_EMITTED",
                        reason_code=plan.reason_code,
                        selected_side=plan.side,
                        aligned_z=aligned_z,
                        opposite_z=opposite_z,
                    )
                continue

            if index == setup.expiry_index:
                durable = (
                    self._outside(setup, feature)
                    and setup.outside_hold_count >= MIN_OUTSIDE_HOLDS
                    and setup.aligned_flow_sum_z >= CONTINUATION_FLOW_SUM_Z
                )
                if setup.continuation_target_consumed:
                    self.counts["continuation_target_consumed"] += 1
                    reason = "CONTINUATION_TARGET_ALREADY_CONSUMED"
                elif durable:
                    plan = self._continuation_plan(setup, feature, index)
                    self.plans.append(plan)
                    emitted.append(plan)
                    self.counts["resolved_continuation"] += 1
                    self._transition(
                        setup=setup,
                        feature=feature,
                        index=index,
                        event_type="PLAN_EMITTED",
                        reason_code=plan.reason_code,
                        selected_side=plan.side,
                        aligned_z=aligned_z,
                        opposite_z=opposite_z,
                    )
                    continue
                else:
                    self.counts["unresolved"] += 1
                    reason = "NO_DURABLE_ACCEPTANCE_OR_FAILURE"
                self._transition(
                    setup=setup,
                    feature=feature,
                    index=index,
                    event_type="INVALIDATED",
                    reason_code=reason,
                    selected_side=None,
                    aligned_z=aligned_z,
                    opposite_z=opposite_z,
                )
                continue
            remaining.append(setup)
        self.active = remaining

        for plan in new_initiative_plans:
            if plan.response != "CONTINUATION":
                continue
            atr = feature.atr
            if atr is None or atr <= 0.0:
                self.counts["arm_rejected_missing_atr"] += 1
                continue
            self.arm(plan, atr=atr, feature=feature)
        return emitted


def atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)


def run(args: argparse.Namespace) -> int:
    raw = json.loads(args.config.read_text(encoding="utf-8"))
    research = dict(raw["research"])
    execution = dict(raw["execution"])
    start = parse_utc_date(str(research["discovery_week"]))
    end = start + timedelta(days=7)
    warmup = start - timedelta(days=1)
    warmup_ns = int(pd.Timestamp(warmup).as_unit("ns").value)
    start_ns = int(pd.Timestamp(start).as_unit("ns").value)
    end_ns = int(pd.Timestamp(end).as_unit("ns").value)

    records = download_aggtrade_days(
        symbol="BTCUSDT",
        start=warmup,
        end=end,
        cache_dir=args.cache,
        workers=args.workers,
    )
    minute_totals = minute_quote_totals(
        iter_downloads(records),
        start_ns=warmup_ns,
        end_ns=start_ns,
    )
    target = calibrate_target_from_minutes(
        minute_totals,
        minutes_per_event=CLOCK_CALIBRATION_MINUTES,
    )
    bars = list(
        iter_volume_bars(
            iter_downloads(records),
            target_quote_notional=target,
            include_partial=False,
        ),
    )
    detector = ImpactRegimeDetector()
    resolver = ImpactResolutionStateMachine()
    previous_initiatives = 0
    for index, bar in enumerate(bars):
        detector.on_bar(bar)
        new_plans = detector.continuation_plans[previous_initiatives:]
        previous_initiatives = len(detector.continuation_plans)
        resolver.on_feature(
            index=index,
            feature=detector.features[-1],
            new_initiative_plans=new_plans,
        )

    trades, metrics, daily, rejections = simulate(
        features=detector.features,
        plans=resolver.plans,
        evaluation_start_ns=start_ns,
        evaluation_end_ns=end_ns,
        starting_nav=float(execution["starting_nav"]),
        cost=float(execution["all_in_cost_bps_per_side"]) / 10_000.0,
        exit_on_boundary_reacceptance=False,
    )

    output = args.output
    output.mkdir(parents=True, exist_ok=True)
    trades.to_csv(output / "trades.csv", index=False)
    daily.to_csv(output / "daily_nav.csv", index=False)
    rejections.to_csv(output / "rejections.csv", index=False)
    pd.DataFrame(asdict(row) for row in resolver.transitions).to_csv(
        output / "scenario_transitions.csv",
        index=False,
    )
    payload = {
        "candidate": "three-event resolved impact state machine",
        "evaluation_start_utc": start.isoformat(),
        "evaluation_end_utc": end.isoformat(),
        "clock_calibration_minutes": CLOCK_CALIBRATION_MINUTES,
        "target_quote_notional": target,
        "resolution_window_bars": RESOLUTION_WINDOW_BARS,
        "opposite_flow_confirm_z": OPPOSITE_FLOW_CONFIRM_Z,
        "continuation_flow_sum_z": CONTINUATION_FLOW_SUM_Z,
        "inside_depth_atr": INSIDE_DEPTH_ATR,
        "outside_depth_atr": OUTSIDE_DEPTH_ATR,
        "minimum_outside_holds": MIN_OUTSIDE_HOLDS,
        "stop_buffer_atr": STOP_BUFFER_ATR,
        "initiative_plans": len(detector.continuation_plans),
        "resolution_counts": dict(resolver.counts),
        "metrics": metrics,
        "downloads": [record.to_dict() for record in records],
        "long_evaluation_run": False,
    }
    atomic_json(output / "impact_resolution_candidate_summary.json", payload)
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=HERE / "config.json")
    parser.add_argument("--cache", type=Path, default=ROOT / ".cache" / "candidate-01-aggtrades")
    parser.add_argument("--output", type=Path, default=ROOT / "artifacts" / "candidate-01-impact-resolution")
    parser.add_argument("--workers", type=int, default=4)
    return parser


if __name__ == "__main__":
    raise SystemExit(run(build_parser().parse_args()))
