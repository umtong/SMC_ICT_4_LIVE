#!/usr/bin/env python3
"""Cost-resolved intrinsic-time liquidity sweep failure with boundary retest.

This candidate changes the market representation rather than adding another
candlestick filter.  Verified aggregate trades are first grouped into causal
equal-notional event bars calibrated from the immediately preceding completed
UTC day.  Market structure is then defined in directional-change intrinsic
time.

The directional-change threshold is fixed economically, not fitted to PnL:

    minimum price risk for the 65% price-risk share
      = 14 bps round-trip cost * 0.65 / (1 - 0.65) = 26 bps

    intrinsic event threshold
      = 26 bps + one additional 14 bps round-trip cost buffer = 40 bps

A complete short scenario is:

1. A 40-bps downward directional change causally confirms a new swing high.
2. That high swept the prior confirmed swing high, but the confirmation price is
   already back below it.
3. Cumulative aggressive flow into the high was positive and the confirming
   reversal leg's flow was negative, identifying trapped initiative buyers.
4. The nearer opposing swing low is internal liquidity.  The lower of the two
   latest confirmed swing lows is the external sell-side-liquidity objective.
5. For at most 30 minutes, wait for price to retest the swept prior high from
   below and close rejected with aligned negative flow.
6. Enter at the next event open only while the swept boundary still holds.
7. Invalidate beyond every observed sweep/confirmation/retest-path extreme plus
   one 7-bps side-cost buffer; target the external opposing swing liquidity.

Longs are symmetric.  Target consumption before entry cancels the setup.  The
maximum holding time is four wall-clock hours, independent of event-bar
activity.  Execution uses 7 bps per side, current-NAV 3% planned risk,
stop-first bar ambiguity and one global position.  One invocation evaluates
exactly one BTC week.
"""

from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import timedelta
import json
from pathlib import Path
import sys
from typing import Any, Literal

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
from impact_regime_probe import EventFeature, ImpactRegimeDetector, ScenarioPlan, simulate  # noqa: E402


ROUND_TRIP_COST_BPS = 14.0
MINIMUM_PRICE_RISK_FRACTION = 0.65
MINIMUM_COST_RESOLVABLE_RISK_BPS = (
    ROUND_TRIP_COST_BPS
    * MINIMUM_PRICE_RISK_FRACTION
    / (1.0 - MINIMUM_PRICE_RISK_FRACTION)
)
DIRECTIONAL_CHANGE_BPS = MINIMUM_COST_RESOLVABLE_RISK_BPS + ROUND_TRIP_COST_BPS
DIRECTIONAL_CHANGE_FRACTION = DIRECTIONAL_CHANGE_BPS / 10_000.0
CLOCK_CALIBRATION_MINUTES = 1
STOP_BUFFER_BPS = 7.0
STOP_BUFFER_FRACTION = STOP_BUFFER_BPS / 10_000.0
RETEST_WINDOW_MINUTES = 30
TARGET_OPPOSING_PIVOTS = 2
MAXIMUM_HOLD_HOURS = 4
MAXIMUM_HOLD_NS = MAXIMUM_HOLD_HOURS * 60 * 60 * 1_000_000_000


@dataclass(frozen=True, slots=True)
class DirectionalChangeEvent:
    event_type: Literal["UP", "DOWN"]
    confirmation_index: int
    confirmation_time_ns: int
    confirmation_price: float
    pivot_index: int
    pivot_time_ns: int
    pivot_price: float
    trend_start_index: int
    trend_flow_imbalance: float
    reversal_flow_imbalance: float
    path_high: float
    path_low: float


@dataclass(slots=True)
class RetestSetup:
    scenario_id: str
    side: Side
    created_index: int
    created_time_ns: int
    expiry_time_ns: int
    boundary: float
    target_price: float
    path_high: float
    path_low: float
    trend_flow_imbalance: float
    reversal_flow_imbalance: float


@dataclass(frozen=True, slots=True)
class ScenarioTransition:
    scenario_id: str
    event_type: str
    event_index: int
    event_time_ns: int
    reason_code: str
    side: str
    boundary: float
    target_price: float
    path_high: float
    path_low: float
    imbalance_z: float | None
    close: float


class DirectionalChangeDetector:
    """Alternating cost-resolved directional changes on completed event closes."""

    def __init__(self, *, threshold_fraction: float) -> None:
        if threshold_fraction <= 0.0:
            raise ValueError("directional-change threshold must be positive")
        self.threshold_fraction = threshold_fraction
        self.mode = 0
        self.high: float | None = None
        self.low: float | None = None
        self.high_index: int | None = None
        self.low_index: int | None = None
        self.last_confirmation_index = 0
        self.events: list[DirectionalChangeEvent] = []

    @staticmethod
    def _flow_imbalance(
        features: list[EventFeature],
        start: int,
        end_inclusive: int,
    ) -> float:
        rows = features[max(start, 0) : end_inclusive + 1]
        quote = sum(item.bar.quote_notional for item in rows)
        signed = sum(item.bar.signed_quote_notional for item in rows)
        return signed / quote if quote > 0.0 else 0.0

    def _event(
        self,
        *,
        event_type: Literal["UP", "DOWN"],
        confirmation_index: int,
        pivot_index: int,
        features: list[EventFeature],
    ) -> DirectionalChangeEvent:
        confirmation = features[confirmation_index].bar
        pivot = features[pivot_index].bar
        trend_start = self.last_confirmation_index
        event = DirectionalChangeEvent(
            event_type=event_type,
            confirmation_index=confirmation_index,
            confirmation_time_ns=confirmation.end_time_ns,
            confirmation_price=confirmation.close,
            pivot_index=pivot_index,
            pivot_time_ns=pivot.end_time_ns,
            pivot_price=pivot.close,
            trend_start_index=trend_start,
            trend_flow_imbalance=self._flow_imbalance(
                features,
                trend_start,
                pivot_index,
            ),
            reversal_flow_imbalance=self._flow_imbalance(
                features,
                pivot_index,
                confirmation_index,
            ),
            path_high=max(
                item.bar.high
                for item in features[trend_start : confirmation_index + 1]
            ),
            path_low=min(
                item.bar.low
                for item in features[trend_start : confirmation_index + 1]
            ),
        )
        self.events.append(event)
        self.last_confirmation_index = confirmation_index
        return event

    def on_feature(
        self,
        *,
        index: int,
        features: list[EventFeature],
    ) -> DirectionalChangeEvent | None:
        price = features[index].bar.close
        if self.high is None or self.low is None:
            self.high = price
            self.low = price
            self.high_index = index
            self.low_index = index
            return None

        if self.mode == 0:
            if price > self.high:
                self.high = price
                self.high_index = index
            if price < self.low:
                self.low = price
                self.low_index = index
            assert self.high_index is not None
            assert self.low_index is not None
            if price >= self.low * (1.0 + self.threshold_fraction):
                event = self._event(
                    event_type="UP",
                    confirmation_index=index,
                    pivot_index=self.low_index,
                    features=features,
                )
                self.mode = 1
                self.high = price
                self.high_index = index
                return event
            if price <= self.high * (1.0 - self.threshold_fraction):
                event = self._event(
                    event_type="DOWN",
                    confirmation_index=index,
                    pivot_index=self.high_index,
                    features=features,
                )
                self.mode = -1
                self.low = price
                self.low_index = index
                return event
            return None

        if self.mode == 1:
            if price > self.high:
                self.high = price
                self.high_index = index
                return None
            assert self.high_index is not None
            if price <= self.high * (1.0 - self.threshold_fraction):
                event = self._event(
                    event_type="DOWN",
                    confirmation_index=index,
                    pivot_index=self.high_index,
                    features=features,
                )
                self.mode = -1
                self.low = price
                self.low_index = index
                return event
            return None

        if price < self.low:
            self.low = price
            self.low_index = index
            return None
        assert self.low_index is not None
        if price >= self.low * (1.0 + self.threshold_fraction):
            event = self._event(
                event_type="UP",
                confirmation_index=index,
                pivot_index=self.low_index,
                features=features,
            )
            self.mode = 1
            self.high = price
            self.high_index = index
            return event
        return None


class FailedSweepRetestStateMachine:
    """Convert confirmed intrinsic-time sweep failures into executable plans."""

    def __init__(self) -> None:
        self.detector = DirectionalChangeDetector(
            threshold_fraction=DIRECTIONAL_CHANGE_FRACTION,
        )
        self.high_events: list[DirectionalChangeEvent] = []
        self.low_events: list[DirectionalChangeEvent] = []
        self.active: list[RetestSetup] = []
        self.plans: list[ScenarioPlan] = []
        self.transitions: list[ScenarioTransition] = []
        self.counts: Counter[str] = Counter()

    def _transition(
        self,
        *,
        setup: RetestSetup,
        feature: EventFeature,
        index: int,
        event_type: str,
        reason_code: str,
    ) -> None:
        self.transitions.append(
            ScenarioTransition(
                scenario_id=setup.scenario_id,
                event_type=event_type,
                event_index=index,
                event_time_ns=feature.bar.end_time_ns,
                reason_code=reason_code,
                side=setup.side.value,
                boundary=setup.boundary,
                target_price=setup.target_price,
                path_high=setup.path_high,
                path_low=setup.path_low,
                imbalance_z=feature.imbalance_z,
                close=feature.bar.close,
            ),
        )

    def _arm_from_event(
        self,
        *,
        event: DirectionalChangeEvent,
        feature: EventFeature,
    ) -> None:
        if event.event_type == "DOWN":
            if not self.high_events or len(self.low_events) < TARGET_OPPOSING_PIVOTS:
                self.counts["insufficient_confirmed_liquidity"] += 1
                self.high_events.append(event)
                return
            prior_same = self.high_events[-1]
            sweep = event.pivot_price > prior_same.pivot_price
            reentered = event.confirmation_price < prior_same.pivot_price
            flow = (
                event.trend_flow_imbalance > 0.0
                and event.reversal_flow_imbalance < 0.0
            )
            side = Side.SHORT
            target = min(
                item.pivot_price
                for item in self.low_events[-TARGET_OPPOSING_PIVOTS:]
            )
            boundary = prior_same.pivot_price
            self.high_events.append(event)
        else:
            if not self.low_events or len(self.high_events) < TARGET_OPPOSING_PIVOTS:
                self.counts["insufficient_confirmed_liquidity"] += 1
                self.low_events.append(event)
                return
            prior_same = self.low_events[-1]
            sweep = event.pivot_price < prior_same.pivot_price
            reentered = event.confirmation_price > prior_same.pivot_price
            flow = (
                event.trend_flow_imbalance < 0.0
                and event.reversal_flow_imbalance > 0.0
            )
            side = Side.LONG
            target = max(
                item.pivot_price
                for item in self.high_events[-TARGET_OPPOSING_PIVOTS:]
            )
            boundary = prior_same.pivot_price
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
        target_untouched = (
            feature.bar.low > target
            if side is Side.SHORT
            else feature.bar.high < target
        )
        if not target_untouched:
            self.counts["external_target_already_consumed"] += 1
            return

        setup = RetestSetup(
            scenario_id=(
                f"dc-failed-sweep:{event.confirmation_index}:"
                f"{side.value.lower()}:{event.confirmation_time_ns}"
            ),
            side=side,
            created_index=event.confirmation_index,
            created_time_ns=event.confirmation_time_ns,
            expiry_time_ns=(
                event.confirmation_time_ns
                + RETEST_WINDOW_MINUTES * 60 * 1_000_000_000
            ),
            boundary=boundary,
            target_price=target,
            path_high=event.path_high,
            path_low=event.path_low,
            trend_flow_imbalance=event.trend_flow_imbalance,
            reversal_flow_imbalance=event.reversal_flow_imbalance,
        )
        self.active.append(setup)
        self.counts["armed"] += 1
        self._transition(
            setup=setup,
            feature=feature,
            index=event.confirmation_index,
            event_type="ARMED",
            reason_code="INTRINSIC_SWEEP_FAILED_WITH_FLOW_REVERSAL",
        )

    @staticmethod
    def _target_touched(setup: RetestSetup, feature: EventFeature) -> bool:
        return (
            feature.bar.low <= setup.target_price
            if setup.side is Side.SHORT
            else feature.bar.high >= setup.target_price
        )

    @staticmethod
    def _retest_confirmed(setup: RetestSetup, feature: EventFeature) -> bool:
        z = feature.imbalance_z
        if z is None or setup.side.sign * z <= 0.0:
            return False
        if setup.side is Side.SHORT:
            return (
                feature.bar.high >= setup.boundary
                and feature.bar.close < setup.boundary
            )
        return (
            feature.bar.low <= setup.boundary
            and feature.bar.close > setup.boundary
        )

    @staticmethod
    def _plan(
        setup: RetestSetup,
        feature: EventFeature,
        index: int,
    ) -> ScenarioPlan:
        stop = (
            setup.path_high * (1.0 + STOP_BUFFER_FRACTION)
            if setup.side is Side.SHORT
            else setup.path_low * (1.0 - STOP_BUFFER_FRACTION)
        )
        return ScenarioPlan(
            scenario_id=setup.scenario_id + f":retest:{index}",
            response="EXHAUSTION_REVERSAL",
            side=setup.side,
            signal_bar_index=index,
            signal_time_ns=feature.bar.end_time_ns,
            stop_price=stop,
            target_price=setup.target_price,
            confirmation_hold_price=setup.boundary,
            structure_high=max(setup.path_high, setup.boundary),
            structure_low=min(setup.path_low, setup.target_price),
            structure_midpoint=0.5 * (setup.boundary + setup.target_price),
            pulse_high=setup.path_high,
            pulse_low=setup.path_low,
            pulse_flow_score=setup.trend_flow_imbalance,
            pulse_move_atr=0.0,
            pulse_path_efficiency=0.0,
            pulse_close_location=0.0,
            reason_code="INTRINSIC_SWEEP_BOUNDARY_RETEST_REJECTED",
        )

    def on_feature(
        self,
        *,
        index: int,
        features: list[EventFeature],
    ) -> list[ScenarioPlan]:
        feature = features[index]
        emitted: list[ScenarioPlan] = []
        remaining: list[RetestSetup] = []
        for setup in self.active:
            if index <= setup.created_index:
                remaining.append(setup)
                continue
            if feature.bar.end_time_ns > setup.expiry_time_ns:
                self.counts["retest_expired"] += 1
                self._transition(
                    setup=setup,
                    feature=feature,
                    index=index,
                    event_type="INVALIDATED",
                    reason_code="BOUNDARY_RETEST_WINDOW_EXPIRED",
                )
                continue
            setup.path_high = max(setup.path_high, feature.bar.high)
            setup.path_low = min(setup.path_low, feature.bar.low)
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
            if self._retest_confirmed(setup, feature):
                plan = self._plan(setup, feature, index)
                self.plans.append(plan)
                emitted.append(plan)
                self.counts["retest_confirmed"] += 1
                self._transition(
                    setup=setup,
                    feature=feature,
                    index=index,
                    event_type="PLAN_EMITTED",
                    reason_code=plan.reason_code,
                )
                continue
            remaining.append(setup)
        self.active = remaining

        event = self.detector.on_feature(index=index, features=features)
        if event is not None:
            self.counts[f"directional_change_{event.event_type.lower()}"] += 1
            self._arm_from_event(event=event, feature=feature)
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

    feature_detector = ImpactRegimeDetector()
    scenario = FailedSweepRetestStateMachine()
    for bar in bars:
        feature_detector.on_bar(bar)
        index = len(feature_detector.features) - 1
        scenario.on_feature(index=index, features=feature_detector.features)

    evaluation_plans = [
        plan
        for plan in scenario.plans
        if start_ns <= plan.signal_time_ns < end_ns
    ]
    trades, metrics, daily, rejections = simulate(
        features=feature_detector.features,
        plans=evaluation_plans,
        evaluation_start_ns=start_ns,
        evaluation_end_ns=end_ns,
        starting_nav=float(execution["starting_nav"]),
        cost=float(execution["all_in_cost_bps_per_side"]) / 10_000.0,
        exit_on_boundary_reacceptance=False,
        maximum_hold_ns=MAXIMUM_HOLD_NS,
    )
    evaluation_bars = [bar for bar in bars if start_ns <= bar.end_time_ns < end_ns]

    output = args.output
    output.mkdir(parents=True, exist_ok=True)
    trades.to_csv(output / "trades.csv", index=False)
    daily.to_csv(output / "daily_nav.csv", index=False)
    rejections.to_csv(output / "rejections.csv", index=False)
    pd.DataFrame(asdict(row) for row in scenario.detector.events).to_csv(
        output / "directional_change_events.csv",
        index=False,
    )
    pd.DataFrame(asdict(row) for row in scenario.transitions).to_csv(
        output / "scenario_transitions.csv",
        index=False,
    )
    payload = {
        "candidate": "cost-resolved intrinsic-time failed sweep retest",
        "evaluation_start_utc": evaluation_start.isoformat(),
        "evaluation_end_utc": evaluation_end.isoformat(),
        "warmup_start_utc": warmup_start.isoformat(),
        "clock_calibration_minutes": CLOCK_CALIBRATION_MINUTES,
        "target_quote_notional": target_quote,
        "evaluation_event_bars": len(evaluation_bars),
        "event_bars_per_day": len(evaluation_bars) / 7.0,
        "round_trip_cost_bps": ROUND_TRIP_COST_BPS,
        "minimum_cost_resolvable_risk_bps": MINIMUM_COST_RESOLVABLE_RISK_BPS,
        "directional_change_bps": DIRECTIONAL_CHANGE_BPS,
        "stop_buffer_bps": STOP_BUFFER_BPS,
        "retest_window_minutes": RETEST_WINDOW_MINUTES,
        "target_opposing_pivots": TARGET_OPPOSING_PIVOTS,
        "maximum_hold_hours": MAXIMUM_HOLD_HOURS,
        "scenario_counts": dict(scenario.counts),
        "directional_change_events": len(scenario.detector.events),
        "plans": len(evaluation_plans),
        "metrics": metrics,
        "downloads": [record.to_dict() for record in records],
        "long_evaluation_run": False,
    }
    atomic_json(output / "directional_change_failed_sweep_week_summary.json", payload)
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--week", required=True)
    parser.add_argument("--config", type=Path, default=HERE / "config.json")
    parser.add_argument("--cache", type=Path, default=ROOT / ".cache" / "candidate-01-dc-aggtrades")
    parser.add_argument("--output", type=Path, default=ROOT / "artifacts" / "candidate-01-directional-change")
    parser.add_argument("--workers", type=int, default=4)
    return parser


if __name__ == "__main__":
    raise SystemExit(run(build_parser().parse_args()))
