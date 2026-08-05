#!/usr/bin/env python3
"""Causal intrinsic-time failed sweep routed by external liquidity hierarchy.

This version keeps the cost-resolved 40-bps directional-change detector and the
failed-sweep boundary-retest scenario, but limits that local response to a
coherent higher-order auction state.

At the completed retest signal:

1. Price must be in discount/premium of the trailing completed 24-hour dealing
   range and in the outer quartile of the trailing completed 72-hour range.
2. If 72-hour net delivery has moved more than half of that range against the
   proposed reversal, a 160-bps directional-change structure (four economic
   40-bps events) must already have shifted in the trade direction.
3. The target is the nearest still-unconsumed, already-confirmed opposing
   intrinsic swing whose signal-close geometry clears the frozen cost-net
   reward/risk gate.  The next event open is never used to choose the target.

The detector, scenario, target hierarchy and execution remain separate.  One
invocation evaluates exactly one BTC week at 3% current-NAV planned risk, 7 bps
per side, one global position and a four-hour wall-clock maximum hold.
"""

from __future__ import annotations

import argparse
from bisect import bisect_left
from collections import Counter
from dataclasses import asdict, dataclass, replace
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
from directional_change_failed_sweep_week import (  # noqa: E402
    CLOCK_CALIBRATION_MINUTES,
    DIRECTIONAL_CHANGE_FRACTION,
    FailedSweepRetestStateMachine,
    MAXIMUM_HOLD_NS,
    DirectionalChangeDetector,
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
class PlanDecision:
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
    if start_index > end_index:
        return None
    rows = features[start_index : end_index + 1]
    if not rows:
        return None
    start_time_ns = rows[0].bar.start_time_ns
    # Require a complete trailing window rather than silently shortening it.
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


def _target_geometry(
    *,
    side: Side,
    expected_entry: float,
    stop: float,
    target: float,
    cost: float,
) -> tuple[float, float, float]:
    price_risk = abs(expected_entry - stop)
    planned_loss = price_risk + expected_entry * cost + stop * cost
    planned_gain = (
        abs(target - expected_entry)
        - expected_entry * cost
        - target * cost
    )
    net_rr = planned_gain / planned_loss if planned_loss > 0.0 else -1.0
    return planned_loss, planned_gain, net_rr


def select_open_liquidity_target(
    *,
    plan: ScenarioPlan,
    features: list[EventFeature],
    events: list[Any],
    cost: float,
    minimum_net_reward_risk: float,
) -> tuple[ScenarioPlan | None, int | None, float | None]:
    signal_index = plan.signal_bar_index
    expected_entry = features[signal_index].bar.close
    event_type = "DOWN" if plan.side is Side.LONG else "UP"
    candidates: list[tuple[float, float, int, float]] = []
    for event in events:
        if event.event_type != event_type:
            continue
        if event.confirmation_index > signal_index:
            continue
        target = float(event.pivot_price)
        if plan.side is Side.LONG and target <= expected_entry:
            continue
        if plan.side is Side.SHORT and target >= expected_entry:
            continue
        subsequent = features[event.confirmation_index + 1 : signal_index + 1]
        consumed = (
            any(row.bar.high >= target for row in subsequent)
            if plan.side is Side.LONG
            else any(row.bar.low <= target for row in subsequent)
        )
        if consumed:
            continue
        _, planned_gain, net_rr = _target_geometry(
            side=plan.side,
            expected_entry=expected_entry,
            stop=plan.stop_price,
            target=target,
            cost=cost,
        )
        if planned_gain <= 0.0 or net_rr < minimum_net_reward_risk:
            continue
        candidates.append(
            (abs(target - expected_entry), target, event.confirmation_index, net_rr),
        )
    if not candidates:
        return None, None, None
    _, target, event_index, net_rr = sorted(candidates)[0]
    updated = replace(
        plan,
        scenario_id=(
            plan.scenario_id
            + f":open-liquidity:{event_index}"
        ),
        target_price=target,
        structure_high=max(plan.structure_high, target),
        structure_low=min(plan.structure_low, target),
        structure_midpoint=0.5 * (plan.confirmation_hold_price + target),
        reason_code="INTRINSIC_SWEEP_RETEST_EXTERNAL_POOL_ROUTED",
    )
    return updated, event_index, net_rr


def route_plan(
    *,
    plan: ScenarioPlan,
    features: list[EventFeature],
    end_times: list[int],
    outer_states: list[str],
    events: list[Any],
    cost: float,
    minimum_net_reward_risk: float,
) -> tuple[ScenarioPlan | None, PlanDecision]:
    index = plan.signal_bar_index
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
        return None, PlanDecision(
            scenario_id=plan.scenario_id,
            signal_index=index,
            signal_time_ns=plan.signal_time_ns,
            side=plan.side.value,
            accepted=False,
            reason_code="INCOMPLETE_DEALING_RANGE_HISTORY",
            short_location=None,
            long_location=None,
            aligned_long_delivery_fraction=None,
            outer_state=outer_state,
            selected_target=None,
            selected_target_event_index=None,
            expected_net_reward_risk=None,
        )
    if short_range.width <= 0.0 or long_range.width <= 0.0:
        reason = "ZERO_WIDTH_DEALING_RANGE"
        return None, PlanDecision(
            scenario_id=plan.scenario_id,
            signal_index=index,
            signal_time_ns=plan.signal_time_ns,
            side=plan.side.value,
            accepted=False,
            reason_code=reason,
            short_location=None,
            long_location=None,
            aligned_long_delivery_fraction=None,
            outer_state=outer_state,
            selected_target=None,
            selected_target_event_index=None,
            expected_net_reward_risk=None,
        )
    signal_close = features[index].bar.close
    short_location = (
        signal_close - short_range.low
    ) / short_range.width
    long_location = (
        signal_close - long_range.low
    ) / long_range.width
    aligned_delivery = (
        plan.side.sign * long_range.delivery / long_range.width
    )
    correct_short_half = (
        short_location <= 0.50
        if plan.side is Side.LONG
        else short_location >= 0.50
    )
    correct_long_quartile = (
        long_location <= LONG_RANGE_OUTER_FRACTION
        if plan.side is Side.LONG
        else long_location >= 1.0 - LONG_RANGE_OUTER_FRACTION
    )
    aligned_outer_state = (
        outer_state == "BULL"
        if plan.side is Side.LONG
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
        routed, target_event_index, net_rr = select_open_liquidity_target(
            plan=plan,
            features=features,
            events=events,
            cost=cost,
            minimum_net_reward_risk=minimum_net_reward_risk,
        )
        if routed is not None:
            return routed, PlanDecision(
                scenario_id=plan.scenario_id,
                signal_index=index,
                signal_time_ns=plan.signal_time_ns,
                side=plan.side.value,
                accepted=True,
                reason_code=routed.reason_code,
                short_location=short_location,
                long_location=long_location,
                aligned_long_delivery_fraction=aligned_delivery,
                outer_state=outer_state,
                selected_target=routed.target_price,
                selected_target_event_index=target_event_index,
                expected_net_reward_risk=net_rr,
            )
        reason = "NO_UNCONSUMED_EXTERNAL_POOL_WITH_NET_GEOMETRY"
    return None, PlanDecision(
        scenario_id=plan.scenario_id,
        signal_index=index,
        signal_time_ns=plan.signal_time_ns,
        side=plan.side.value,
        accepted=False,
        reason_code=reason,
        short_location=short_location,
        long_location=long_location,
        aligned_long_delivery_fraction=aligned_delivery,
        outer_state=outer_state,
        selected_target=None,
        selected_target_event_index=None,
        expected_net_reward_risk=None,
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
    base_scenario = FailedSweepRetestStateMachine()
    for bar in bars:
        feature_detector.on_bar(bar)
        index = len(feature_detector.features) - 1
        base_scenario.on_feature(index=index, features=feature_detector.features)

    features = feature_detector.features
    end_times = [row.bar.end_time_ns for row in features]
    outer_states = outer_state_series(features)
    cost = float(execution["all_in_cost_bps_per_side"]) / 10_000.0
    minimum_net_rr = float(execution["minimum_net_reward_risk"])
    evaluation_base_plans = [
        plan
        for plan in base_scenario.plans
        if start_ns <= plan.signal_time_ns < end_ns
    ]
    routed_plans: list[ScenarioPlan] = []
    decisions: list[PlanDecision] = []
    for plan in evaluation_base_plans:
        routed, decision = route_plan(
            plan=plan,
            features=features,
            end_times=end_times,
            outer_states=outer_states,
            events=base_scenario.detector.events,
            cost=cost,
            minimum_net_reward_risk=minimum_net_rr,
        )
        decisions.append(decision)
        if routed is not None:
            routed_plans.append(routed)

    trades, metrics, daily, rejections = simulate(
        features=features,
        plans=routed_plans,
        evaluation_start_ns=start_ns,
        evaluation_end_ns=end_ns,
        starting_nav=float(execution["starting_nav"]),
        cost=cost,
        exit_on_boundary_reacceptance=False,
        maximum_hold_ns=MAXIMUM_HOLD_NS,
    )
    evaluation_bars = [
        row.bar for row in features if start_ns <= row.bar.end_time_ns < end_ns
    ]
    decision_counts = Counter(row.reason_code for row in decisions)

    output = args.output
    output.mkdir(parents=True, exist_ok=True)
    trades.to_csv(output / "trades.csv", index=False)
    daily.to_csv(output / "daily_nav.csv", index=False)
    rejections.to_csv(output / "rejections.csv", index=False)
    pd.DataFrame(asdict(row) for row in decisions).to_csv(
        output / "plan_decisions.csv",
        index=False,
    )
    pd.DataFrame(asdict(row) for row in base_scenario.detector.events).to_csv(
        output / "directional_change_events.csv",
        index=False,
    )
    payload = {
        "candidate": "intrinsic failed sweep with causal external-liquidity router",
        "evaluation_start_utc": evaluation_start.isoformat(),
        "evaluation_end_utc": evaluation_end.isoformat(),
        "context_start_utc": context_start.isoformat(),
        "calibration_start_utc": calibration_start.isoformat(),
        "clock_calibration_minutes": CLOCK_CALIBRATION_MINUTES,
        "target_quote_notional": target_quote,
        "evaluation_event_bars": len(evaluation_bars),
        "event_bars_per_day": len(evaluation_bars) / 7.0,
        "base_plans": len(evaluation_base_plans),
        "routed_plans": len(routed_plans),
        "decision_counts": dict(decision_counts),
        "scenario_counts": dict(base_scenario.counts),
        "router": {
            "short_range_hours": SHORT_RANGE_HOURS,
            "long_range_hours": LONG_RANGE_HOURS,
            "long_range_outer_fraction": LONG_RANGE_OUTER_FRACTION,
            "strong_against_delivery_fraction": STRONG_AGAINST_DELIVERY_FRACTION,
            "outer_directional_change_multiple": OUTER_DIRECTIONAL_CHANGE_MULTIPLE,
            "outer_directional_change_fraction": OUTER_DIRECTIONAL_CHANGE_FRACTION,
            "target_policy": "nearest unconsumed confirmed opposing pivot clearing signal-close net RR",
        },
        "risk_fraction": 0.03,
        "all_in_cost_bps_per_side": float(execution["all_in_cost_bps_per_side"]),
        "maximum_hold_hours": MAXIMUM_HOLD_NS / NS_PER_HOUR,
        "metrics": metrics,
        "downloads": [record.to_dict() for record in records],
        "long_evaluation_run": False,
    }
    atomic_json(output / "intrinsic_external_liquidity_week_summary.json", payload)
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--week", required=True)
    parser.add_argument("--config", type=Path, default=HERE / "config.json")
    parser.add_argument(
        "--cache",
        type=Path,
        default=ROOT / ".cache" / "candidate-01-intrinsic-external",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "artifacts" / "candidate-01-intrinsic-external",
    )
    parser.add_argument("--workers", type=int, default=4)
    return parser


if __name__ == "__main__":
    raise SystemExit(run(build_parser().parse_args()))
