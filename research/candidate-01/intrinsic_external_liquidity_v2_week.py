#!/usr/bin/env python3
"""Intrinsic failed-sweep detector separated from external target routing.

Version 1 inherited the old two-pivot target from the local scenario before the
new external-liquidity router ran.  Consequently an otherwise valid sweep and
boundary retest could be cancelled merely because that obsolete target traded
first.  This file removes that architectural coupling.

Ordered causal state:

    cost-resolved 40-bps directional-change pivot
    -> same-side liquidity sweep and close back through the prior pivot
    -> aggregate-flow reversal
    -> rejection of the swept boundary within 30 minutes
    -> 24h premium/discount and 72h external-liquidity context
    -> optional 160-bps outer CHoCH when delivery strongly opposes the trade
    -> nearest unconsumed confirmed opposing pivot with cost-net geometry
    -> next-event market entry, path-extreme stop, four-hour maximum hold

The detector emits no target and cannot inspect target consumption.  The router
chooses a target only after the completed retest signal, using only information
available at that signal close.  One invocation evaluates exactly one BTC week
at 3% current-NAV planned risk and 7 bps per side.
"""

from __future__ import annotations

import argparse
from bisect import bisect_left
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

from aggtrade_clock import calibrate_target_from_minutes, iter_volume_bars, minute_quote_totals  # noqa: E402
from aggtrade_data import download_aggtrade_days, iter_downloads  # noqa: E402
from core import Side  # noqa: E402
from data import parse_utc_date  # noqa: E402
from directional_change_failed_sweep_week import (  # noqa: E402
    CLOCK_CALIBRATION_MINUTES,
    DIRECTIONAL_CHANGE_FRACTION,
    DirectionalChangeDetector,
    DirectionalChangeEvent,
    MAXIMUM_HOLD_NS,
    RETEST_WINDOW_MINUTES,
    STOP_BUFFER_FRACTION,
)
from impact_regime_probe import EventFeature, ImpactRegimeDetector, ScenarioPlan, simulate  # noqa: E402


CONTEXT_WARMUP_DAYS = 4
SHORT_RANGE_HOURS = 24
LONG_RANGE_HOURS = 72
LONG_RANGE_OUTER_FRACTION = 0.25
STRONG_AGAINST_DELIVERY_FRACTION = 0.50
OUTER_DIRECTIONAL_CHANGE_MULTIPLE = 4.0
OUTER_DIRECTIONAL_CHANGE_FRACTION = (
    DIRECTIONAL_CHANGE_FRACTION * OUTER_DIRECTIONAL_CHANGE_MULTIPLE
)
NS_PER_HOUR = 60 * 60 * 1_000_000_000


@dataclass(slots=True)
class SweepRetestSetup:
    scenario_id: str
    side: Side
    created_index: int
    created_time_ns: int
    expiry_time_ns: int
    boundary: float
    path_high: float
    path_low: float
    trend_flow_imbalance: float
    reversal_flow_imbalance: float


@dataclass(frozen=True, slots=True)
class SweepRetestSignal:
    scenario_id: str
    side: Side
    signal_bar_index: int
    signal_time_ns: int
    boundary: float
    stop_price: float
    path_high: float
    path_low: float
    trend_flow_imbalance: float
    reversal_flow_imbalance: float


@dataclass(frozen=True, slots=True)
class DetectorTransition:
    scenario_id: str
    event_type: str
    event_index: int
    event_time_ns: int
    reason_code: str
    side: str
    boundary: float
    path_high: float
    path_low: float
    imbalance_z: float | None
    close: float


class TargetFreeSweepRetestDetector:
    """Detect local failed sweeps and retests without owning target logic."""

    def __init__(self) -> None:
        self.detector = DirectionalChangeDetector(
            threshold_fraction=DIRECTIONAL_CHANGE_FRACTION,
        )
        self.high_events: list[DirectionalChangeEvent] = []
        self.low_events: list[DirectionalChangeEvent] = []
        self.active: list[SweepRetestSetup] = []
        self.signals: list[SweepRetestSignal] = []
        self.transitions: list[DetectorTransition] = []
        self.counts: Counter[str] = Counter()

    def _transition(
        self,
        *,
        setup: SweepRetestSetup,
        feature: EventFeature,
        index: int,
        event_type: str,
        reason_code: str,
    ) -> None:
        self.transitions.append(
            DetectorTransition(
                scenario_id=setup.scenario_id,
                event_type=event_type,
                event_index=index,
                event_time_ns=feature.bar.end_time_ns,
                reason_code=reason_code,
                side=setup.side.value,
                boundary=setup.boundary,
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
            prior_same = self.high_events[-1] if self.high_events else None
            self.high_events.append(event)
            if prior_same is None:
                self.counts["insufficient_same_side_liquidity"] += 1
                return
            sweep = event.pivot_price > prior_same.pivot_price
            reentered = event.confirmation_price < prior_same.pivot_price
            flow = (
                event.trend_flow_imbalance > 0.0
                and event.reversal_flow_imbalance < 0.0
            )
            side = Side.SHORT
            boundary = prior_same.pivot_price
        else:
            prior_same = self.low_events[-1] if self.low_events else None
            self.low_events.append(event)
            if prior_same is None:
                self.counts["insufficient_same_side_liquidity"] += 1
                return
            sweep = event.pivot_price < prior_same.pivot_price
            reentered = event.confirmation_price > prior_same.pivot_price
            flow = (
                event.trend_flow_imbalance < 0.0
                and event.reversal_flow_imbalance > 0.0
            )
            side = Side.LONG
            boundary = prior_same.pivot_price

        if not sweep:
            self.counts["no_same_side_liquidity_sweep"] += 1
            return
        if not reentered:
            self.counts["outside_value_retained"] += 1
            return
        if not flow:
            self.counts["order_flow_did_not_reverse"] += 1
            return

        setup = SweepRetestSetup(
            scenario_id=(
                f"dc-target-free:{event.confirmation_index}:"
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
    def _retest_confirmed(
        setup: SweepRetestSetup,
        feature: EventFeature,
    ) -> bool:
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
    def _signal(
        setup: SweepRetestSetup,
        feature: EventFeature,
        index: int,
    ) -> SweepRetestSignal:
        stop = (
            setup.path_high * (1.0 + STOP_BUFFER_FRACTION)
            if setup.side is Side.SHORT
            else setup.path_low * (1.0 - STOP_BUFFER_FRACTION)
        )
        return SweepRetestSignal(
            scenario_id=setup.scenario_id + f":retest:{index}",
            side=setup.side,
            signal_bar_index=index,
            signal_time_ns=feature.bar.end_time_ns,
            boundary=setup.boundary,
            stop_price=stop,
            path_high=setup.path_high,
            path_low=setup.path_low,
            trend_flow_imbalance=setup.trend_flow_imbalance,
            reversal_flow_imbalance=setup.reversal_flow_imbalance,
        )

    def on_feature(
        self,
        *,
        index: int,
        features: list[EventFeature],
    ) -> list[SweepRetestSignal]:
        feature = features[index]
        emitted: list[SweepRetestSignal] = []
        remaining: list[SweepRetestSetup] = []
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
            if self._retest_confirmed(setup, feature):
                signal = self._signal(setup, feature, index)
                self.signals.append(signal)
                emitted.append(signal)
                self.counts["retest_confirmed"] += 1
                self._transition(
                    setup=setup,
                    feature=feature,
                    index=index,
                    event_type="SIGNAL_EMITTED",
                    reason_code="INTRINSIC_SWEEP_BOUNDARY_RETEST_REJECTED",
                )
                continue
            remaining.append(setup)
        self.active = remaining

        event = self.detector.on_feature(index=index, features=features)
        if event is not None:
            self.counts[f"directional_change_{event.event_type.lower()}"] += 1
            self._arm_from_event(event=event, feature=feature)
        return emitted


@dataclass(frozen=True, slots=True)
class CompletedRange:
    start_index: int
    end_index: int
    start_time_ns: int
    end_time_ns: int
    open: float
    high: float
    low: float
    close: float

    @property
    def width(self) -> float:
        return self.high - self.low

    @property
    def delivery(self) -> float:
        return self.close - self.open


@dataclass(frozen=True, slots=True)
class RoutingDecision:
    scenario_id: str
    signal_index: int
    signal_time_ns: int
    side: str
    accepted: bool
    reason_code: str
    short_location: float | None
    long_location: float | None
    aligned_long_delivery_fraction: float | None
    outer_state: str
    selected_target: float | None
    selected_target_event_index: int | None
    expected_price_risk_fraction: float | None
    expected_net_reward_risk: float | None


def completed_range(
    features: list[EventFeature],
    end_times: list[int],
    *,
    end_index: int,
    hours: int,
) -> CompletedRange | None:
    end_time_ns = end_times[end_index]
    cutoff = end_time_ns - hours * NS_PER_HOUR
    start_index = bisect_left(end_times, cutoff, 0, end_index + 1)
    rows = features[start_index : end_index + 1]
    if not rows:
        return None
    start_time_ns = rows[0].bar.start_time_ns
    if end_time_ns - start_time_ns < hours * NS_PER_HOUR:
        return None
    return CompletedRange(
        start_index=start_index,
        end_index=end_index,
        start_time_ns=start_time_ns,
        end_time_ns=end_time_ns,
        open=rows[0].bar.open,
        high=max(row.bar.high for row in rows),
        low=min(row.bar.low for row in rows),
        close=rows[-1].bar.close,
    )


def outer_state_series(features: list[EventFeature]) -> list[str]:
    detector = DirectionalChangeDetector(
        threshold_fraction=OUTER_DIRECTIONAL_CHANGE_FRACTION,
    )
    highs: list[float] = []
    lows: list[float] = []
    state = "UNKNOWN"
    result: list[str] = []
    for index in range(len(features)):
        event = detector.on_feature(index=index, features=features)
        if event is not None:
            if event.event_type == "DOWN":
                highs.append(event.pivot_price)
            else:
                lows.append(event.pivot_price)
            if len(highs) >= 2 and len(lows) >= 2:
                if highs[-1] > highs[-2] and lows[-1] > lows[-2]:
                    state = "BULL"
                elif highs[-1] < highs[-2] and lows[-1] < lows[-2]:
                    state = "BEAR"
                else:
                    state = "BALANCE"
        result.append(state)
    return result


def target_geometry(
    *,
    expected_entry: float,
    stop: float,
    target: float,
    cost: float,
) -> tuple[float, float, float, float]:
    price_risk = abs(expected_entry - stop)
    planned_loss = price_risk + expected_entry * cost + stop * cost
    planned_gain = (
        abs(target - expected_entry)
        - expected_entry * cost
        - target * cost
    )
    price_fraction = price_risk / planned_loss if planned_loss > 0.0 else 0.0
    net_rr = planned_gain / planned_loss if planned_loss > 0.0 else -1.0
    return planned_loss, planned_gain, price_fraction, net_rr


def select_target(
    *,
    signal: SweepRetestSignal,
    features: list[EventFeature],
    events: list[DirectionalChangeEvent],
    cost: float,
    minimum_price_risk_fraction: float,
    minimum_net_reward_risk: float,
) -> tuple[ScenarioPlan | None, int | None, float | None, float | None]:
    index = signal.signal_bar_index
    expected_entry = features[index].bar.close
    target_event_type = "DOWN" if signal.side is Side.LONG else "UP"
    candidates: list[tuple[float, float, int, float, float]] = []
    for event in events:
        if event.event_type != target_event_type:
            continue
        if event.confirmation_index > index:
            continue
        target = float(event.pivot_price)
        if signal.side is Side.LONG and target <= expected_entry:
            continue
        if signal.side is Side.SHORT and target >= expected_entry:
            continue
        subsequent = features[event.confirmation_index + 1 : index + 1]
        consumed = (
            any(row.bar.high >= target for row in subsequent)
            if signal.side is Side.LONG
            else any(row.bar.low <= target for row in subsequent)
        )
        if consumed:
            continue
        _, planned_gain, price_fraction, net_rr = target_geometry(
            expected_entry=expected_entry,
            stop=signal.stop_price,
            target=target,
            cost=cost,
        )
        if (
            price_fraction < minimum_price_risk_fraction
            or planned_gain <= 0.0
            or net_rr < minimum_net_reward_risk
        ):
            continue
        candidates.append(
            (
                abs(target - expected_entry),
                target,
                event.confirmation_index,
                price_fraction,
                net_rr,
            ),
        )
    if not candidates:
        return None, None, None, None
    _, target, event_index, price_fraction, net_rr = sorted(candidates)[0]
    plan = ScenarioPlan(
        scenario_id=(
            signal.scenario_id
            + f":open-liquidity:{event_index}"
        ),
        response="EXHAUSTION_REVERSAL",
        side=signal.side,
        signal_bar_index=signal.signal_bar_index,
        signal_time_ns=signal.signal_time_ns,
        stop_price=signal.stop_price,
        target_price=target,
        confirmation_hold_price=signal.boundary,
        structure_high=max(signal.path_high, signal.boundary, target),
        structure_low=min(signal.path_low, signal.boundary, target),
        structure_midpoint=0.5 * (signal.boundary + target),
        pulse_high=signal.path_high,
        pulse_low=signal.path_low,
        pulse_flow_score=signal.trend_flow_imbalance,
        pulse_move_atr=0.0,
        pulse_path_efficiency=0.0,
        pulse_close_location=0.0,
        reason_code="TARGET_FREE_SWEEP_RETEST_EXTERNAL_POOL_ROUTED",
    )
    return plan, event_index, price_fraction, net_rr


def route_signal(
    *,
    signal: SweepRetestSignal,
    features: list[EventFeature],
    end_times: list[int],
    outer_states: list[str],
    events: list[DirectionalChangeEvent],
    cost: float,
    minimum_price_risk_fraction: float,
    minimum_net_reward_risk: float,
) -> tuple[ScenarioPlan | None, RoutingDecision]:
    index = signal.signal_bar_index
    short_range = completed_range(
        features,
        end_times,
        end_index=index,
        hours=SHORT_RANGE_HOURS,
    )
    long_range = completed_range(
        features,
        end_times,
        end_index=index,
        hours=LONG_RANGE_HOURS,
    )
    outer_state = outer_states[index]
    if short_range is None or long_range is None:
        return None, RoutingDecision(
            signal.scenario_id,
            index,
            signal.signal_time_ns,
            signal.side.value,
            False,
            "INCOMPLETE_DEALING_RANGE_HISTORY",
            None,
            None,
            None,
            outer_state,
            None,
            None,
            None,
            None,
        )
    if short_range.width <= 0.0 or long_range.width <= 0.0:
        return None, RoutingDecision(
            signal.scenario_id,
            index,
            signal.signal_time_ns,
            signal.side.value,
            False,
            "ZERO_WIDTH_DEALING_RANGE",
            None,
            None,
            None,
            outer_state,
            None,
            None,
            None,
            None,
        )
    signal_close = features[index].bar.close
    short_location = (signal_close - short_range.low) / short_range.width
    long_location = (signal_close - long_range.low) / long_range.width
    aligned_delivery = signal.side.sign * long_range.delivery / long_range.width
    correct_short_half = (
        short_location <= 0.50
        if signal.side is Side.LONG
        else short_location >= 0.50
    )
    correct_long_quartile = (
        long_location <= LONG_RANGE_OUTER_FRACTION
        if signal.side is Side.LONG
        else long_location >= 1.0 - LONG_RANGE_OUTER_FRACTION
    )
    aligned_outer_state = (
        outer_state == "BULL"
        if signal.side is Side.LONG
        else outer_state == "BEAR"
    )
    delivery_ok = (
        aligned_delivery >= -STRONG_AGAINST_DELIVERY_FRACTION
        or aligned_outer_state
    )
    if not correct_short_half:
        reason = "WRONG_24H_PREMIUM_DISCOUNT"
    elif not correct_long_quartile:
        reason = "NOT_72H_EXTERNAL_LIQUIDITY"
    elif not delivery_ok:
        reason = "STRONG_72H_DELIVERY_WITHOUT_OUTER_CHOCH"
    else:
        plan, target_index, price_fraction, net_rr = select_target(
            signal=signal,
            features=features,
            events=events,
            cost=cost,
            minimum_price_risk_fraction=minimum_price_risk_fraction,
            minimum_net_reward_risk=minimum_net_reward_risk,
        )
        if plan is not None:
            return plan, RoutingDecision(
                signal.scenario_id,
                index,
                signal.signal_time_ns,
                signal.side.value,
                True,
                plan.reason_code,
                short_location,
                long_location,
                aligned_delivery,
                outer_state,
                plan.target_price,
                target_index,
                price_fraction,
                net_rr,
            )
        reason = "NO_UNCONSUMED_EXTERNAL_POOL_WITH_NET_GEOMETRY"
    return None, RoutingDecision(
        signal.scenario_id,
        index,
        signal.signal_time_ns,
        signal.side.value,
        False,
        reason,
        short_location,
        long_location,
        aligned_delivery,
        outer_state,
        None,
        None,
        None,
        None,
    )


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
    context_start = evaluation_start - timedelta(days=CONTEXT_WARMUP_DAYS)
    calibration_start = evaluation_start - timedelta(days=1)
    calibration_start_ns = int(pd.Timestamp(calibration_start).as_unit("ns").value)
    start_ns = int(pd.Timestamp(evaluation_start).as_unit("ns").value)
    end_ns = int(pd.Timestamp(evaluation_end).as_unit("ns").value)

    records = download_aggtrade_days(
        symbol="BTCUSDT",
        start=context_start,
        end=evaluation_end,
        cache_dir=args.cache,
        workers=args.workers,
    )
    calibration_minutes = minute_quote_totals(
        iter_downloads(records),
        start_ns=calibration_start_ns,
        end_ns=start_ns,
    )
    target_quote = calibrate_target_from_minutes(
        calibration_minutes,
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
    detector = TargetFreeSweepRetestDetector()
    for bar in bars:
        feature_detector.on_bar(bar)
        index = len(feature_detector.features) - 1
        detector.on_feature(index=index, features=feature_detector.features)

    features = feature_detector.features
    end_times = [row.bar.end_time_ns for row in features]
    outer_states = outer_state_series(features)
    cost = float(execution["all_in_cost_bps_per_side"]) / 10_000.0
    minimum_price_fraction = float(execution["minimum_price_risk_fraction"])
    minimum_net_rr = float(execution["minimum_net_reward_risk"])
    evaluation_signals = [
        signal
        for signal in detector.signals
        if start_ns <= signal.signal_time_ns < end_ns
    ]
    plans: list[ScenarioPlan] = []
    decisions: list[RoutingDecision] = []
    for signal in evaluation_signals:
        plan, decision = route_signal(
            signal=signal,
            features=features,
            end_times=end_times,
            outer_states=outer_states,
            events=detector.detector.events,
            cost=cost,
            minimum_price_risk_fraction=minimum_price_fraction,
            minimum_net_reward_risk=minimum_net_rr,
        )
        decisions.append(decision)
        if plan is not None:
            plans.append(plan)

    trades, metrics, daily, rejections = simulate(
        features=features,
        plans=plans,
        evaluation_start_ns=start_ns,
        evaluation_end_ns=end_ns,
        starting_nav=float(execution["starting_nav"]),
        cost=cost,
        exit_on_boundary_reacceptance=False,
        maximum_hold_ns=MAXIMUM_HOLD_NS,
    )
    decision_counts = Counter(row.reason_code for row in decisions)
    evaluation_bars = [
        row.bar for row in features if start_ns <= row.bar.end_time_ns < end_ns
    ]

    output = args.output
    output.mkdir(parents=True, exist_ok=True)
    trades.to_csv(output / "trades.csv", index=False)
    daily.to_csv(output / "daily_nav.csv", index=False)
    rejections.to_csv(output / "rejections.csv", index=False)
    pd.DataFrame(asdict(row) for row in evaluation_signals).to_csv(
        output / "retest_signals.csv",
        index=False,
    )
    pd.DataFrame(asdict(row) for row in decisions).to_csv(
        output / "routing_decisions.csv",
        index=False,
    )
    pd.DataFrame(asdict(row) for row in detector.detector.events).to_csv(
        output / "directional_change_events.csv",
        index=False,
    )
    pd.DataFrame(asdict(row) for row in detector.transitions).to_csv(
        output / "detector_transitions.csv",
        index=False,
    )
    payload = {
        "candidate": "target-free intrinsic sweep detector with causal external-liquidity router",
        "evaluation_start_utc": evaluation_start.isoformat(),
        "evaluation_end_utc": evaluation_end.isoformat(),
        "context_start_utc": context_start.isoformat(),
        "calibration_start_utc": calibration_start.isoformat(),
        "clock_calibration_minutes": CLOCK_CALIBRATION_MINUTES,
        "target_quote_notional": target_quote,
        "evaluation_event_bars": len(evaluation_bars),
        "event_bars_per_day": len(evaluation_bars) / 7.0,
        "evaluation_retest_signals": len(evaluation_signals),
        "routed_plans": len(plans),
        "decision_counts": dict(decision_counts),
        "detector_counts": dict(detector.counts),
        "router": {
            "short_range_hours": SHORT_RANGE_HOURS,
            "long_range_hours": LONG_RANGE_HOURS,
            "long_range_outer_fraction": LONG_RANGE_OUTER_FRACTION,
            "strong_against_delivery_fraction": STRONG_AGAINST_DELIVERY_FRACTION,
            "outer_directional_change_multiple": OUTER_DIRECTIONAL_CHANGE_MULTIPLE,
            "outer_directional_change_fraction": OUTER_DIRECTIONAL_CHANGE_FRACTION,
            "target_policy": "nearest unconsumed confirmed opposing pivot clearing signal-close cost and net RR",
        },
        "risk_fraction": 0.03,
        "all_in_cost_bps_per_side": float(execution["all_in_cost_bps_per_side"]),
        "maximum_hold_hours": MAXIMUM_HOLD_NS / NS_PER_HOUR,
        "metrics": metrics,
        "downloads": [record.to_dict() for record in records],
        "long_evaluation_run": False,
    }
    atomic_json(output / "intrinsic_external_liquidity_v2_week_summary.json", payload)
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--week", required=True)
    parser.add_argument("--config", type=Path, default=HERE / "config.json")
    parser.add_argument(
        "--cache",
        type=Path,
        default=ROOT / ".cache" / "candidate-01-intrinsic-external-v2",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "artifacts" / "candidate-01-intrinsic-external-v2",
    )
    parser.add_argument("--workers", type=int, default=4)
    return parser


if __name__ == "__main__":
    raise SystemExit(run(build_parser().parse_args()))
