#!/usr/bin/env python3
"""Rolling two-hour dealing-range failed-auction rotation.

The detector and trading scenario are separate.  On UTC-aligned five-minute
aggregate-trade bars it maintains a completed 24-bar (two-hour) dealing range.
A scenario is armed only when:

1. price trades at least 0.10 ATR beyond one external edge;
2. the completed bar closes at least 0.05 ATR back inside the range;
3. signed aggressive flow z >= 0.50 in the sweep direction, proving that the
   external liquidity was actively taken rather than merely touched.

Within the next two completed bars, opposite flow z >= 0.50 and a close through
the sweep bar midpoint confirm failed auction.  Entry is the next five-minute
open while the swept boundary remains rejected.  Invalidation is beyond every
observed sweep/confirmation-path extreme plus 0.15 ATR.  The target is the
pre-sweep dealing-range equilibrium; a target touched before confirmation
cancels the setup.  A six-bar post-signal cooldown prevents repeated trades on
the same liquidity event.

The fixed clock gives identical opportunity resolution across years.  Execution
uses 7 bps per side, current-NAV 3% planned risk, stop-first ambiguity and one
global position.  One invocation evaluates exactly one BTC week.
"""

from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import timedelta
import json
from pathlib import Path
from statistics import median
import sys
from typing import Any

import pandas as pd

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
SRC = ROOT / "src"
for item in (HERE, SRC):
    if str(item) not in sys.path:
        sys.path.insert(0, str(item))

from aggtrade_data import download_aggtrade_days, iter_downloads  # noqa: E402
from aggtrade_time_clock import iter_time_bars  # noqa: E402
from core import Side  # noqa: E402
from data import parse_utc_date  # noqa: E402
from impact_regime_probe import EventFeature, ImpactRegimeDetector, ScenarioPlan, simulate  # noqa: E402


TIMEFRAME_MINUTES = 5
RANGE_BARS = 24
MIN_RANGE_WIDTH_ATR = 1.50
MIN_SWEEP_ATR = 0.10
MIN_REENTRY_ATR = 0.05
SWEEP_FLOW_Z = 0.50
OPPOSITE_FLOW_Z = 0.50
CONFIRMATION_WINDOW_BARS = 2
MIDPOINT_BREAK_ATR = 0.00
STOP_BUFFER_ATR = 0.15
COOLDOWN_BARS = 6


@dataclass(slots=True)
class SweepSetup:
    scenario_id: str
    side: Side
    created_index: int
    expiry_index: int
    atr: float
    boundary: float
    range_high: float
    range_low: float
    range_midpoint: float
    sweep_bar_midpoint: float
    path_high: float
    path_low: float


@dataclass(frozen=True, slots=True)
class SweepTransition:
    scenario_id: str
    event_type: str
    event_index: int
    event_time_ns: int
    reason_code: str
    side: str
    boundary: float
    range_midpoint: float
    sweep_bar_midpoint: float
    path_high: float
    path_low: float
    flow_z: float | None
    close: float


class RollingRangeSweepStateMachine:
    def __init__(self) -> None:
        self.active: SweepSetup | None = None
        self.cooldown_until = -1
        self.plans: list[ScenarioPlan] = []
        self.transitions: list[SweepTransition] = []
        self.counts: Counter[str] = Counter()

    def _transition(
        self,
        *,
        setup: SweepSetup,
        feature: EventFeature,
        index: int,
        event_type: str,
        reason_code: str,
    ) -> None:
        self.transitions.append(
            SweepTransition(
                scenario_id=setup.scenario_id,
                event_type=event_type,
                event_index=index,
                event_time_ns=feature.bar.end_time_ns,
                reason_code=reason_code,
                side=setup.side.value,
                boundary=setup.boundary,
                range_midpoint=setup.range_midpoint,
                sweep_bar_midpoint=setup.sweep_bar_midpoint,
                path_high=setup.path_high,
                path_low=setup.path_low,
                flow_z=feature.imbalance_z,
                close=feature.bar.close,
            ),
        )

    def _arm(
        self,
        *,
        index: int,
        feature: EventFeature,
        side: Side,
        boundary: float,
        range_high: float,
        range_low: float,
        atr: float,
    ) -> None:
        bar = feature.bar
        setup = SweepSetup(
            scenario_id=(
                f"rolling-range-sweep:{index}:{side.value.lower()}:"
                f"{bar.end_time_ns}"
            ),
            side=side,
            created_index=index,
            expiry_index=index + CONFIRMATION_WINDOW_BARS,
            atr=atr,
            boundary=boundary,
            range_high=range_high,
            range_low=range_low,
            range_midpoint=0.5 * (range_high + range_low),
            sweep_bar_midpoint=0.5 * (bar.high + bar.low),
            path_high=bar.high,
            path_low=bar.low,
        )
        self.active = setup
        self.counts["armed"] += 1
        self._transition(
            setup=setup,
            feature=feature,
            index=index,
            event_type="ARMED",
            reason_code="EXTERNAL_LIQUIDITY_SWEPT_AND_REJECTED",
        )

    @staticmethod
    def _target_touched(setup: SweepSetup, feature: EventFeature) -> bool:
        return (
            feature.bar.low <= setup.range_midpoint
            if setup.side is Side.SHORT
            else feature.bar.high >= setup.range_midpoint
        )

    @staticmethod
    def _boundary_failed(setup: SweepSetup, feature: EventFeature) -> bool:
        return (
            feature.bar.close > setup.boundary + MIN_REENTRY_ATR * setup.atr
            if setup.side is Side.SHORT
            else feature.bar.close < setup.boundary - MIN_REENTRY_ATR * setup.atr
        )

    @staticmethod
    def _confirmed(setup: SweepSetup, feature: EventFeature) -> bool:
        z = feature.imbalance_z
        if z is None:
            return False
        aligned_opposite = setup.side.sign * z >= OPPOSITE_FLOW_Z
        midpoint_break = (
            feature.bar.close
            <= setup.sweep_bar_midpoint - MIDPOINT_BREAK_ATR * setup.atr
            if setup.side is Side.SHORT
            else feature.bar.close
            >= setup.sweep_bar_midpoint + MIDPOINT_BREAK_ATR * setup.atr
        )
        return aligned_opposite and midpoint_break

    @staticmethod
    def _plan(setup: SweepSetup, feature: EventFeature, index: int) -> ScenarioPlan:
        stop = (
            setup.path_high + STOP_BUFFER_ATR * setup.atr
            if setup.side is Side.SHORT
            else setup.path_low - STOP_BUFFER_ATR * setup.atr
        )
        return ScenarioPlan(
            scenario_id=setup.scenario_id + f":confirm:{index}",
            response="EXHAUSTION_REVERSAL",
            side=setup.side,
            signal_bar_index=index,
            signal_time_ns=feature.bar.end_time_ns,
            stop_price=stop,
            target_price=setup.range_midpoint,
            confirmation_hold_price=setup.boundary,
            structure_high=setup.range_high,
            structure_low=setup.range_low,
            structure_midpoint=setup.range_midpoint,
            pulse_high=setup.path_high,
            pulse_low=setup.path_low,
            pulse_flow_score=0.0,
            pulse_move_atr=0.0,
            pulse_path_efficiency=0.0,
            pulse_close_location=0.0,
            reason_code="ROLLING_RANGE_FAILED_AUCTION_CONFIRMED",
        )

    def on_feature(
        self,
        *,
        index: int,
        feature: EventFeature,
        prior_features: list[EventFeature],
    ) -> list[ScenarioPlan]:
        emitted: list[ScenarioPlan] = []
        setup = self.active
        if setup is not None and index > setup.created_index:
            if index > setup.expiry_index:
                self.counts["expired"] += 1
                self._transition(
                    setup=setup,
                    feature=feature,
                    index=index,
                    event_type="INVALIDATED",
                    reason_code="CONFIRMATION_WINDOW_EXPIRED",
                )
                self.active = None
            else:
                setup.path_high = max(setup.path_high, feature.bar.high)
                setup.path_low = min(setup.path_low, feature.bar.low)
                if self._target_touched(setup, feature):
                    self.counts["target_consumed"] += 1
                    self._transition(
                        setup=setup,
                        feature=feature,
                        index=index,
                        event_type="INVALIDATED",
                        reason_code="EQUILIBRIUM_REACHED_BEFORE_ENTRY",
                    )
                    self.active = None
                elif self._boundary_failed(setup, feature):
                    self.counts["boundary_failed"] += 1
                    self._transition(
                        setup=setup,
                        feature=feature,
                        index=index,
                        event_type="INVALIDATED",
                        reason_code="OUTSIDE_VALUE_REACCEPTED",
                    )
                    self.active = None
                elif self._confirmed(setup, feature):
                    plan = self._plan(setup, feature, index)
                    self.plans.append(plan)
                    emitted.append(plan)
                    self.counts["confirmed"] += 1
                    self.cooldown_until = index + COOLDOWN_BARS
                    self._transition(
                        setup=setup,
                        feature=feature,
                        index=index,
                        event_type="PLAN_EMITTED",
                        reason_code=plan.reason_code,
                    )
                    self.active = None
                elif index == setup.expiry_index:
                    self.counts["expired"] += 1
                    self._transition(
                        setup=setup,
                        feature=feature,
                        index=index,
                        event_type="INVALIDATED",
                        reason_code="CONFIRMATION_WINDOW_EXPIRED",
                    )
                    self.active = None

        if self.active is not None or index <= self.cooldown_until:
            if index <= self.cooldown_until:
                self.counts["cooldown"] += 1
            return emitted
        if len(prior_features) < RANGE_BARS:
            self.counts["insufficient_history"] += 1
            return emitted
        atr = feature.atr
        z = feature.imbalance_z
        if atr is None or atr <= 0.0 or z is None:
            self.counts["state_unavailable"] += 1
            return emitted

        history = prior_features[-RANGE_BARS:]
        range_high = max(item.bar.high for item in history)
        range_low = min(item.bar.low for item in history)
        width = range_high - range_low
        if width < MIN_RANGE_WIDTH_ATR * atr:
            self.counts["range_too_narrow"] += 1
            return emitted
        bar = feature.bar
        high_sweep = (
            bar.high >= range_high + MIN_SWEEP_ATR * atr
            and bar.close <= range_high - MIN_REENTRY_ATR * atr
            and z >= SWEEP_FLOW_Z
        )
        low_sweep = (
            bar.low <= range_low - MIN_SWEEP_ATR * atr
            and bar.close >= range_low + MIN_REENTRY_ATR * atr
            and z <= -SWEEP_FLOW_Z
        )
        if high_sweep and low_sweep:
            self.counts["ambiguous_sweep"] += 1
            return emitted
        if high_sweep:
            self._arm(
                index=index,
                feature=feature,
                side=Side.SHORT,
                boundary=range_high,
                range_high=range_high,
                range_low=range_low,
                atr=atr,
            )
        elif low_sweep:
            self._arm(
                index=index,
                feature=feature,
                side=Side.LONG,
                boundary=range_low,
                range_high=range_high,
                range_low=range_low,
                atr=atr,
            )
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
    warmup_start = evaluation_start - timedelta(days=1)
    start_ns = int(pd.Timestamp(evaluation_start).as_unit("ns").value)
    end_ns = int(pd.Timestamp(evaluation_end).as_unit("ns").value)

    records = download_aggtrade_days(
        symbol="BTCUSDT",
        start=warmup_start,
        end=evaluation_end,
        cache_dir=args.cache,
        workers=args.workers,
    )
    bars = list(
        iter_time_bars(
            iter_downloads(records),
            interval_minutes=TIMEFRAME_MINUTES,
            include_partial=False,
        ),
    )
    feature_detector = ImpactRegimeDetector()
    scenario = RollingRangeSweepStateMachine()
    plans: list[ScenarioPlan] = []
    for bar in bars:
        prior = list(feature_detector.features)
        feature_detector.on_bar(bar)
        feature = feature_detector.features[-1]
        plans.extend(
            scenario.on_feature(
                index=len(feature_detector.features) - 1,
                feature=feature,
                prior_features=prior,
            ),
        )

    trades, metrics, daily, rejections = simulate(
        features=feature_detector.features,
        plans=plans,
        evaluation_start_ns=start_ns,
        evaluation_end_ns=end_ns,
        starting_nav=float(execution["starting_nav"]),
        cost=float(execution["all_in_cost_bps_per_side"]) / 10_000.0,
        exit_on_boundary_reacceptance=False,
    )
    evaluation_bars = [bar for bar in bars if start_ns <= bar.end_time_ns < end_ns]
    range_bps = [bar.range_fraction * 10_000.0 for bar in evaluation_bars]

    output = args.output
    output.mkdir(parents=True, exist_ok=True)
    trades.to_csv(output / "trades.csv", index=False)
    daily.to_csv(output / "daily_nav.csv", index=False)
    rejections.to_csv(output / "rejections.csv", index=False)
    pd.DataFrame(asdict(row) for row in scenario.transitions).to_csv(
        output / "scenario_transitions.csv",
        index=False,
    )
    payload = {
        "candidate": "rolling two-hour failed-auction equilibrium rotation",
        "evaluation_start_utc": evaluation_start.isoformat(),
        "evaluation_end_utc": evaluation_end.isoformat(),
        "timeframe_minutes": TIMEFRAME_MINUTES,
        "range_bars": RANGE_BARS,
        "range_minutes": RANGE_BARS * TIMEFRAME_MINUTES,
        "evaluation_bars": len(evaluation_bars),
        "bars_per_day": len(evaluation_bars) / 7.0,
        "median_bar_range_bps": float(median(range_bps)) if range_bps else None,
        "scenario_parameters": {
            "minimum_range_width_atr": MIN_RANGE_WIDTH_ATR,
            "minimum_sweep_atr": MIN_SWEEP_ATR,
            "minimum_reentry_atr": MIN_REENTRY_ATR,
            "sweep_flow_z": SWEEP_FLOW_Z,
            "opposite_flow_z": OPPOSITE_FLOW_Z,
            "confirmation_window_bars": CONFIRMATION_WINDOW_BARS,
            "stop_buffer_atr": STOP_BUFFER_ATR,
            "cooldown_bars": COOLDOWN_BARS,
        },
        "scenario_counts": dict(scenario.counts),
        "plans": len(plans),
        "metrics": metrics,
        "downloads": [record.to_dict() for record in records],
        "long_evaluation_run": False,
    }
    atomic_json(output / "rolling_range_sweep_week_summary.json", payload)
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--week", required=True)
    parser.add_argument("--config", type=Path, default=HERE / "config.json")
    parser.add_argument("--cache", type=Path, default=ROOT / ".cache" / "candidate-01-timebar-aggtrades")
    parser.add_argument("--output", type=Path, default=ROOT / "artifacts" / "candidate-01-rolling-range-sweep")
    parser.add_argument("--workers", type=int, default=4)
    return parser


if __name__ == "__main__":
    raise SystemExit(run(build_parser().parse_args()))
