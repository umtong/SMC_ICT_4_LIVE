#!/usr/bin/env python3
"""Causal aggregate-trade impact-failure reversal candidate.

This module promotes the only first-week impact response that passed the
research gate into an online state machine.  A completed initiative pulse is
first classified by ``ImpactRegimeDetector`` as efficient outside value.  That
does not trigger a trade.  It arms a short-lived failed-impact scenario:

    efficient initiative impact beyond prior external liquidity
    -> price closes back inside the broken boundary
    -> opposite aggregate-trade flow appears
    -> price closes through the initiative pulse midpoint
    -> next completed event opens while the boundary failure still holds
    -> enter opposite the original initiative
    -> invalidate beyond the complete failed-impact path extreme
    -> target the opposite edge of the pre-impact structure

The detector and scenario are separate.  The state machine consumes one
completed equal-notional event bar at a time and cannot revise an emitted plan
when future bars arrive.

Two online variants are evaluated on the first frozen BTC week:

* ``online-parity`` reproduces the prior diagnostic exactly and exists only to
  prove that the offline diagnosis can be expressed causally;
* ``online-strict`` is the executable candidate.  It includes every
  intermediate confirmation-window extreme in the stop and rejects a setup if
  its liquidity target was already consumed before entry.

All execution remains in the already verified event-bar simulator: next-event
entry, confirmation hold, 7 bps per side, 3% current-NAV risk, stop-first
ambiguity and one global position.  No second week or long evaluation is run by
this program.
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

from aggtrade_clock import (  # noqa: E402
    calibrate_target_from_minutes,
    iter_volume_bars,
    minute_quote_totals,
)
from aggtrade_data import download_aggtrade_days, iter_downloads  # noqa: E402
from core import Side  # noqa: E402
from data import parse_utc_date  # noqa: E402
from impact_continuation_diagnostics import derived_plans  # noqa: E402
from impact_regime_probe import (  # noqa: E402
    CLOCK_CALIBRATION_MINUTES,
    EventFeature,
    ImpactRegimeDetector,
    ScenarioPlan,
    simulate,
)


CONFIRMATION_WINDOW_BARS = 3
OPPOSITE_FLOW_CONFIRM_Z = 0.50
INSIDE_DEPTH_ATR = 0.05
STOP_BUFFER_ATR = 0.15
RISK_RATE = 0.03


@dataclass(slots=True)
class ImpactFailureSetup:
    initiative_plan: ScenarioPlan
    created_index: int
    expiry_index: int
    atr: float
    reversal_side: Side
    boundary: float
    pulse_midpoint: float
    path_high: float
    path_low: float
    target_price: float
    target_consumed: bool = False


@dataclass(frozen=True, slots=True)
class ImpactFailureTransition:
    scenario_id: str
    event_type: str
    event_index: int
    event_time_ns: int
    observed_time_ns: int
    reason_code: str
    outward_side: str
    reversal_side: str
    boundary: float
    pulse_midpoint: float
    path_high: float
    path_low: float
    target_price: float
    aligned_opposite_flow_z: float | None
    close: float


class ImpactFailureStateMachine:
    """Online failed-impact response to completed efficient initiative pulses."""

    def __init__(
        self,
        *,
        include_intermediate_extremes: bool,
        reject_consumed_target: bool,
    ) -> None:
        self.include_intermediate_extremes = include_intermediate_extremes
        self.reject_consumed_target = reject_consumed_target
        self.active: list[ImpactFailureSetup] = []
        self.plans: list[ScenarioPlan] = []
        self.transitions: list[ImpactFailureTransition] = []
        self.counts: Counter[str] = Counter()

    @staticmethod
    def _reversal_side(outward_side: Side) -> Side:
        return Side.SHORT if outward_side is Side.LONG else Side.LONG

    @staticmethod
    def _target(plan: ScenarioPlan, reversal_side: Side) -> float:
        return plan.structure_low if reversal_side is Side.SHORT else plan.structure_high

    def _transition(
        self,
        *,
        setup: ImpactFailureSetup,
        feature: EventFeature,
        index: int,
        event_type: str,
        reason_code: str,
        aligned_opposite_flow_z: float | None,
    ) -> None:
        self.transitions.append(
            ImpactFailureTransition(
                scenario_id=setup.initiative_plan.scenario_id,
                event_type=event_type,
                event_index=index,
                event_time_ns=feature.bar.end_time_ns,
                observed_time_ns=feature.bar.end_time_ns,
                reason_code=reason_code,
                outward_side=setup.initiative_plan.side.value,
                reversal_side=setup.reversal_side.value,
                boundary=setup.boundary,
                pulse_midpoint=setup.pulse_midpoint,
                path_high=setup.path_high,
                path_low=setup.path_low,
                target_price=setup.target_price,
                aligned_opposite_flow_z=aligned_opposite_flow_z,
                close=feature.bar.close,
            ),
        )

    def arm(self, plan: ScenarioPlan, *, atr: float) -> None:
        if plan.response != "CONTINUATION":
            return
        if atr <= 0.0:
            raise ValueError("impact-failure setup requires positive ATR")
        reversal_side = self._reversal_side(plan.side)
        setup = ImpactFailureSetup(
            initiative_plan=plan,
            created_index=plan.signal_bar_index,
            expiry_index=plan.signal_bar_index + CONFIRMATION_WINDOW_BARS,
            atr=atr,
            reversal_side=reversal_side,
            boundary=plan.confirmation_hold_price,
            pulse_midpoint=0.5 * (plan.pulse_high + plan.pulse_low),
            path_high=plan.pulse_high,
            path_low=plan.pulse_low,
            target_price=self._target(plan, reversal_side),
        )
        self.active.append(setup)
        self.counts["armed"] += 1

    def _target_touched(self, setup: ImpactFailureSetup, feature: EventFeature) -> bool:
        return (
            feature.bar.low <= setup.target_price
            if setup.reversal_side is Side.SHORT
            else feature.bar.high >= setup.target_price
        )

    def _confirmed(
        self,
        setup: ImpactFailureSetup,
        feature: EventFeature,
    ) -> tuple[bool, float | None]:
        z = feature.imbalance_z
        opposite_z = setup.reversal_side.sign * z if z is not None else None
        if setup.initiative_plan.side is Side.LONG:
            inside = feature.bar.close <= setup.boundary - INSIDE_DEPTH_ATR * setup.atr
            midpoint_break = feature.bar.close < setup.pulse_midpoint
        else:
            inside = feature.bar.close >= setup.boundary + INSIDE_DEPTH_ATR * setup.atr
            midpoint_break = feature.bar.close > setup.pulse_midpoint
        confirmed = (
            opposite_z is not None
            and opposite_z >= OPPOSITE_FLOW_CONFIRM_Z
            and inside
            and midpoint_break
        )
        return confirmed, opposite_z

    def _build_plan(
        self,
        setup: ImpactFailureSetup,
        *,
        feature: EventFeature,
        index: int,
    ) -> ScenarioPlan:
        stop = (
            setup.path_high + STOP_BUFFER_ATR * setup.atr
            if setup.reversal_side is Side.SHORT
            else setup.path_low - STOP_BUFFER_ATR * setup.atr
        )
        source = setup.initiative_plan
        return ScenarioPlan(
            scenario_id=source.scenario_id + f":decay-reversal:{index}",
            response="EXHAUSTION_REVERSAL",
            side=setup.reversal_side,
            signal_bar_index=index,
            signal_time_ns=feature.bar.end_time_ns,
            stop_price=stop,
            target_price=setup.target_price,
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
            reason_code="EFFICIENT_IMPACT_DECAY_REVERSED",
        )

    def on_feature(
        self,
        *,
        index: int,
        feature: EventFeature,
        new_initiative_plans: Iterable[ScenarioPlan] = (),
    ) -> list[ScenarioPlan]:
        emitted: list[ScenarioPlan] = []
        remaining: list[ImpactFailureSetup] = []
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
                    reason_code="CONFIRMATION_WINDOW_EXPIRED",
                    aligned_opposite_flow_z=None,
                )
                continue

            if self.include_intermediate_extremes:
                setup.path_high = max(setup.path_high, feature.bar.high)
                setup.path_low = min(setup.path_low, feature.bar.low)
            else:
                # Diagnostic parity used only the initiative pulse and the
                # actual confirmation bar, not non-confirming intermediate bars.
                pass

            if self._target_touched(setup, feature):
                setup.target_consumed = True
                if self.reject_consumed_target:
                    self.counts["target_consumed_before_confirmation"] += 1
                    self._transition(
                        setup=setup,
                        feature=feature,
                        index=index,
                        event_type="INVALIDATED",
                        reason_code="TARGET_LIQUIDITY_ALREADY_CONSUMED",
                        aligned_opposite_flow_z=None,
                    )
                    continue

            confirmed, opposite_z = self._confirmed(setup, feature)
            if confirmed:
                if not self.include_intermediate_extremes:
                    setup.path_high = max(setup.path_high, feature.bar.high)
                    setup.path_low = min(setup.path_low, feature.bar.low)
                plan = self._build_plan(setup, feature=feature, index=index)
                emitted.append(plan)
                self.plans.append(plan)
                self.counts["confirmed"] += 1
                self._transition(
                    setup=setup,
                    feature=feature,
                    index=index,
                    event_type="PLAN_EMITTED",
                    reason_code=plan.reason_code,
                    aligned_opposite_flow_z=opposite_z,
                )
                continue

            if index == setup.expiry_index:
                self.counts["expired"] += 1
                self._transition(
                    setup=setup,
                    feature=feature,
                    index=index,
                    event_type="INVALIDATED",
                    reason_code="CONFIRMATION_WINDOW_EXPIRED",
                    aligned_opposite_flow_z=opposite_z,
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
            self.arm(plan, atr=atr)
            setup = self.active[-1]
            self._transition(
                setup=setup,
                feature=feature,
                index=index,
                event_type="ARMED",
                reason_code="EFFICIENT_INITIATIVE_IMPACT_OBSERVED",
                aligned_opposite_flow_z=None,
            )
        return emitted


def plan_signature(plan: ScenarioPlan) -> tuple[object, ...]:
    return (
        plan.scenario_id,
        plan.response,
        plan.side.value,
        plan.signal_bar_index,
        plan.signal_time_ns,
        round(plan.stop_price, 10),
        round(plan.target_price, 10),
        round(plan.confirmation_hold_price, 10),
    )


def atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)


def run(args: argparse.Namespace) -> int:
    raw = json.loads(args.config.read_text(encoding="utf-8"))
    research = dict(raw["research"])
    execution = dict(raw["execution"])
    evaluation_start = parse_utc_date(str(research["discovery_week"]))
    evaluation_end = evaluation_start + timedelta(days=7)
    warmup_start = evaluation_start - timedelta(days=1)
    warmup_ns = int(pd.Timestamp(warmup_start).as_unit("ns").value)
    start_ns = int(pd.Timestamp(evaluation_start).as_unit("ns").value)
    end_ns = int(pd.Timestamp(evaluation_end).as_unit("ns").value)

    records = download_aggtrade_days(
        symbol="BTCUSDT",
        start=warmup_start,
        end=evaluation_end,
        cache_dir=args.cache,
        workers=args.workers,
    )
    warmup_minutes = minute_quote_totals(
        iter_downloads(records),
        start_ns=warmup_ns,
        end_ns=start_ns,
    )
    target_quote = calibrate_target_from_minutes(
        warmup_minutes,
        minutes_per_event=CLOCK_CALIBRATION_MINUTES,
    )
    bars = list(
        iter_volume_bars(
            iter_downloads(records),
            target_quote_notional=target_quote,
            include_partial=False,
        ),
    )

    detector = ImpactRegimeDetector()
    parity = ImpactFailureStateMachine(
        include_intermediate_extremes=False,
        reject_consumed_target=False,
    )
    strict = ImpactFailureStateMachine(
        include_intermediate_extremes=True,
        reject_consumed_target=True,
    )
    for index, bar in enumerate(bars):
        detector_plans = detector.on_bar(bar)
        feature = detector.features[-1]
        initiatives = [plan for plan in detector_plans if plan.response == "CONTINUATION"]
        parity.on_feature(index=index, feature=feature, new_initiative_plans=initiatives)
        strict.on_feature(index=index, feature=feature, new_initiative_plans=initiatives)

    _, offline_reversal, _ = derived_plans(detector)
    offline_signatures = [plan_signature(plan) for plan in offline_reversal]
    parity_signatures = [plan_signature(plan) for plan in parity.plans]
    parity_exact = offline_signatures == parity_signatures
    if not parity_exact:
        raise RuntimeError(
            "online parity state machine diverged from prior causal diagnosis: "
            f"offline={len(offline_signatures)}, online={len(parity_signatures)}",
        )

    variants = {
        "offline-diagnostic": offline_reversal,
        "online-parity": parity.plans,
        "online-strict": strict.plans,
    }
    output = args.output
    output.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(asdict(row) for row in parity.transitions).to_csv(
        output / "parity_transitions.csv",
        index=False,
    )
    pd.DataFrame(asdict(row) for row in strict.transitions).to_csv(
        output / "strict_transitions.csv",
        index=False,
    )
    results: dict[str, Any] = {}
    for label, plans in variants.items():
        trades, metrics, daily, rejections = simulate(
            features=detector.features,
            plans=plans,
            evaluation_start_ns=start_ns,
            evaluation_end_ns=end_ns,
            starting_nav=float(execution["starting_nav"]),
            cost=float(execution["all_in_cost_bps_per_side"]) / 10_000.0,
        )
        destination = output / label
        destination.mkdir(parents=True, exist_ok=True)
        trades.to_csv(destination / "trades.csv", index=False)
        daily.to_csv(destination / "daily_nav.csv", index=False)
        rejections.to_csv(destination / "rejections.csv", index=False)
        atomic_json(destination / "metrics.json", metrics)
        results[label] = metrics

    payload = {
        "candidate": "causal failed-impact reversal state machine",
        "evaluation_start_utc": evaluation_start.isoformat(),
        "evaluation_end_utc": evaluation_end.isoformat(),
        "clock_calibration_minutes": CLOCK_CALIBRATION_MINUTES,
        "target_quote_notional": target_quote,
        "confirmation_window_bars": CONFIRMATION_WINDOW_BARS,
        "opposite_flow_confirm_z": OPPOSITE_FLOW_CONFIRM_Z,
        "inside_depth_atr": INSIDE_DEPTH_ATR,
        "stop_buffer_atr": STOP_BUFFER_ATR,
        "risk_fraction": RISK_RATE,
        "online_parity_exact": parity_exact,
        "offline_plan_count": len(offline_reversal),
        "online_parity_plan_count": len(parity.plans),
        "online_strict_plan_count": len(strict.plans),
        "parity_state_counts": dict(parity.counts),
        "strict_state_counts": dict(strict.counts),
        "results": results,
        "downloads": [record.to_dict() for record in records],
        "long_evaluation_run": False,
    }
    atomic_json(output / "impact_failure_candidate_summary.json", payload)
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=HERE / "config.json")
    parser.add_argument(
        "--cache",
        type=Path,
        default=ROOT / ".cache" / "candidate-01-aggtrades",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "artifacts" / "candidate-01-impact-failure-candidate",
    )
    parser.add_argument("--workers", type=int, default=4)
    return parser


if __name__ == "__main__":
    raise SystemExit(run(build_parser().parse_args()))
