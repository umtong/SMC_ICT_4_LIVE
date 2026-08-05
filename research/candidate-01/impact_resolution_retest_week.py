#!/usr/bin/env python3
"""Resolved impact with structural acceptance-retest continuation entries.

The 26-bps causal daily information clock and three-event impact resolution are
unchanged.  Failed-impact reversals retain their immediate next-event entry.
Only durable-continuation execution changes:

    durable outside acceptance
    -> do not chase the completed move
    -> wait at most three completed events for the broken boundary to trade
    -> require that event to close back outside the boundary
    -> enter on the next event open while boundary acceptance still holds
    -> invalidate below/above the retest and boundary buffer
    -> retain the original measured external-liquidity target

A target touched before the retest cancels the scenario.  A close back through
the boundary invalidates it.  This is a causal state transition, not an ex-post
entry-price optimization.  One invocation evaluates exactly one BTC week.
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

from adaptive_aggtrade_clock import DEFAULT_CANDIDATE_MINUTES, build_daily_cost_resolved_bars  # noqa: E402
from aggtrade_data import download_aggtrade_days  # noqa: E402
from core import Side  # noqa: E402
from data import parse_utc_date  # noqa: E402
from impact_regime_probe import MINIMUM_PRICE_RISK_FRACTION, EventFeature, ImpactRegimeDetector, ScenarioPlan, simulate  # noqa: E402
from impact_resolution_adaptive_week import MINIMUM_EVENT_RANGE_BPS, ROUND_TRIP_COST_BPS  # noqa: E402
from impact_resolution_candidate import (  # noqa: E402
    INSIDE_DEPTH_ATR,
    RESOLUTION_WINDOW_BARS,
    STOP_BUFFER_ATR,
    ImpactResolutionStateMachine,
)


RETEST_WINDOW_BARS = RESOLUTION_WINDOW_BARS


@dataclass(slots=True)
class AcceptanceRetestSetup:
    source: ScenarioPlan
    created_index: int
    expiry_index: int
    atr: float


@dataclass(frozen=True, slots=True)
class AcceptanceRetestTransition:
    scenario_id: str
    event_type: str
    event_index: int
    event_time_ns: int
    reason_code: str
    side: str
    boundary: float
    target: float
    close: float
    low: float
    high: float


class AcceptanceRetestStateMachine:
    """Turn durable acceptance into a non-chasing boundary-retest entry."""

    def __init__(self) -> None:
        self.active: list[AcceptanceRetestSetup] = []
        self.plans: list[ScenarioPlan] = []
        self.transitions: list[AcceptanceRetestTransition] = []
        self.counts: Counter[str] = Counter()

    def _transition(
        self,
        *,
        setup: AcceptanceRetestSetup,
        feature: EventFeature,
        index: int,
        event_type: str,
        reason_code: str,
    ) -> None:
        self.transitions.append(
            AcceptanceRetestTransition(
                scenario_id=setup.source.scenario_id,
                event_type=event_type,
                event_index=index,
                event_time_ns=feature.bar.end_time_ns,
                reason_code=reason_code,
                side=setup.source.side.value,
                boundary=setup.source.confirmation_hold_price,
                target=setup.source.target_price,
                close=feature.bar.close,
                low=feature.bar.low,
                high=feature.bar.high,
            ),
        )

    def arm(self, plan: ScenarioPlan, *, atr: float, feature: EventFeature, index: int) -> None:
        if plan.response != "CONTINUATION":
            raise ValueError("acceptance retest only accepts continuation plans")
        if atr <= 0.0:
            raise ValueError("acceptance retest requires positive ATR")
        setup = AcceptanceRetestSetup(
            source=plan,
            created_index=index,
            expiry_index=index + RETEST_WINDOW_BARS,
            atr=atr,
        )
        self.active.append(setup)
        self.counts["armed"] += 1
        self._transition(
            setup=setup,
            feature=feature,
            index=index,
            event_type="ARMED",
            reason_code="DURABLE_ACCEPTANCE_WAITING_FOR_BOUNDARY_RETEST",
        )

    @staticmethod
    def _target_touched(setup: AcceptanceRetestSetup, feature: EventFeature) -> bool:
        return (
            feature.bar.high >= setup.source.target_price
            if setup.source.side is Side.LONG
            else feature.bar.low <= setup.source.target_price
        )

    @staticmethod
    def _boundary_failed(setup: AcceptanceRetestSetup, feature: EventFeature) -> bool:
        boundary = setup.source.confirmation_hold_price
        return (
            feature.bar.close < boundary - INSIDE_DEPTH_ATR * setup.atr
            if setup.source.side is Side.LONG
            else feature.bar.close > boundary + INSIDE_DEPTH_ATR * setup.atr
        )

    @staticmethod
    def _retest_confirmed(setup: AcceptanceRetestSetup, feature: EventFeature) -> bool:
        boundary = setup.source.confirmation_hold_price
        if setup.source.side is Side.LONG:
            touched = feature.bar.low <= boundary
            held = feature.bar.close >= boundary + INSIDE_DEPTH_ATR * setup.atr
        else:
            touched = feature.bar.high >= boundary
            held = feature.bar.close <= boundary - INSIDE_DEPTH_ATR * setup.atr
        return touched and held

    @staticmethod
    def _build_plan(setup: AcceptanceRetestSetup, feature: EventFeature, index: int) -> ScenarioPlan:
        source = setup.source
        boundary = source.confirmation_hold_price
        stop = (
            min(feature.bar.low, boundary - STOP_BUFFER_ATR * setup.atr)
            if source.side is Side.LONG
            else max(feature.bar.high, boundary + STOP_BUFFER_ATR * setup.atr)
        )
        return ScenarioPlan(
            scenario_id=source.scenario_id + f":boundary-retest:{index}",
            response="CONTINUATION",
            side=source.side,
            signal_bar_index=index,
            signal_time_ns=feature.bar.end_time_ns,
            stop_price=stop,
            target_price=source.target_price,
            confirmation_hold_price=boundary,
            structure_high=source.structure_high,
            structure_low=source.structure_low,
            structure_midpoint=source.structure_midpoint,
            pulse_high=max(source.pulse_high, feature.bar.high),
            pulse_low=min(source.pulse_low, feature.bar.low),
            pulse_flow_score=source.pulse_flow_score,
            pulse_move_atr=source.pulse_move_atr,
            pulse_path_efficiency=source.pulse_path_efficiency,
            pulse_close_location=source.pulse_close_location,
            reason_code="DURABLE_ACCEPTANCE_BOUNDARY_RETEST_HELD",
        )

    def on_feature(self, *, index: int, feature: EventFeature) -> list[ScenarioPlan]:
        emitted: list[ScenarioPlan] = []
        remaining: list[AcceptanceRetestSetup] = []
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
                    reason_code="RETEST_WINDOW_EXPIRED",
                )
                continue
            if self._target_touched(setup, feature):
                self.counts["target_consumed"] += 1
                self._transition(
                    setup=setup,
                    feature=feature,
                    index=index,
                    event_type="INVALIDATED",
                    reason_code="TARGET_REACHED_BEFORE_RETEST",
                )
                continue
            if self._boundary_failed(setup, feature):
                self.counts["boundary_failed"] += 1
                self._transition(
                    setup=setup,
                    feature=feature,
                    index=index,
                    event_type="INVALIDATED",
                    reason_code="ACCEPTED_BOUNDARY_FAILED_BEFORE_RETEST",
                )
                continue
            if self._retest_confirmed(setup, feature):
                plan = self._build_plan(setup, feature, index)
                emitted.append(plan)
                self.plans.append(plan)
                self.counts["confirmed"] += 1
                self._transition(
                    setup=setup,
                    feature=feature,
                    index=index,
                    event_type="PLAN_EMITTED",
                    reason_code=plan.reason_code,
                )
                continue
            if index == setup.expiry_index:
                self.counts["expired"] += 1
                self._transition(
                    setup=setup,
                    feature=feature,
                    index=index,
                    event_type="INVALIDATED",
                    reason_code="RETEST_WINDOW_EXPIRED",
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
        minimum_range_bps=MINIMUM_EVENT_RANGE_BPS,
        candidate_minutes=DEFAULT_CANDIDATE_MINUTES,
    )

    detector = ImpactRegimeDetector()
    resolver = ImpactResolutionStateMachine()
    retests = AcceptanceRetestStateMachine()
    final_plans: list[ScenarioPlan] = []
    previous_initiatives = 0
    for index, bar in enumerate(bars):
        detector.on_bar(bar)
        feature = detector.features[-1]
        final_plans.extend(retests.on_feature(index=index, feature=feature))
        initiatives = detector.continuation_plans[previous_initiatives:]
        previous_initiatives = len(detector.continuation_plans)
        resolved = resolver.on_feature(
            index=index,
            feature=feature,
            new_initiative_plans=initiatives,
        )
        for plan in resolved:
            if plan.response == "CONTINUATION":
                atr = feature.atr
                if atr is None or atr <= 0.0:
                    retests.counts["arm_rejected_missing_atr"] += 1
                    continue
                retests.arm(plan, atr=atr, feature=feature, index=index)
            else:
                final_plans.append(plan)

    final_plans.sort(key=lambda plan: (plan.signal_bar_index, plan.scenario_id))
    trades, metrics, daily, rejections = simulate(
        features=detector.features,
        plans=final_plans,
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
    pd.DataFrame(asdict(row) for row in resolver.transitions).to_csv(
        output / "resolution_transitions.csv",
        index=False,
    )
    pd.DataFrame(asdict(row) for row in retests.transitions).to_csv(
        output / "retest_transitions.csv",
        index=False,
    )
    pd.DataFrame(item.to_dict() for item in calibrations).to_json(
        output / "daily_clock_calibrations.json",
        orient="records",
        indent=2,
    )
    payload = {
        "candidate": "resolved impact with structural acceptance-retest continuation",
        "evaluation_start_utc": evaluation_start.isoformat(),
        "evaluation_end_utc": evaluation_end.isoformat(),
        "clock_source_start_utc": clock_source_start.isoformat(),
        "minimum_event_range_bps": MINIMUM_EVENT_RANGE_BPS,
        "evaluation_selected_minutes": [item.selected_minutes for item in evaluation_calibrations],
        "evaluation_event_bars": len(evaluation_bars),
        "event_bars_per_day": len(evaluation_bars) / 7.0,
        "resolution_window_bars": RESOLUTION_WINDOW_BARS,
        "retest_window_bars": RETEST_WINDOW_BARS,
        "resolution_counts": dict(resolver.counts),
        "retest_counts": dict(retests.counts),
        "final_plans": len(final_plans),
        "metrics": metrics,
        "downloads": [record.to_dict() for record in records],
        "long_evaluation_run": False,
    }
    atomic_json(output / "impact_resolution_retest_week_summary.json", payload)
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--week", required=True)
    parser.add_argument("--config", type=Path, default=HERE / "config.json")
    parser.add_argument("--cache", type=Path, default=ROOT / ".cache" / "candidate-01-impact-resolution-retest")
    parser.add_argument("--output", type=Path, default=ROOT / "artifacts" / "candidate-01-impact-resolution-retest")
    parser.add_argument("--workers", type=int, default=4)
    return parser


if __name__ == "__main__":
    raise SystemExit(run(build_parser().parse_args()))
