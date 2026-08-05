#!/usr/bin/env python3
"""Controlled post-retest confirmation diagnosis on exactly one BTC week.

The daily causal information clock, target-free sweep detector, 24h/72h router,
external target, stop, cost, 3% NAV sizing and one-position simulator are frozen.
Only the transition after a routed boundary-retest rejection is changed:

``baseline``
    Enter at the next event open, as in the failed Q1 candidate.

``retest-bar-break``
    Within the existing 30-minute scenario window, require a completed event to
    close beyond the rejection event's directional extreme with same-side
    aggregate flow.  Enter on the next event open only if the break still holds.

``new-40bp-directional-change``
    Within the same 30-minute window, require a fresh cost-resolved 40-bps
    directional-change event whose pivot formed no earlier than the retest
    signal.  This is a stronger displacement/MSS confirmation, not a threshold
    search.  Enter on the next event open.

A setup is cancelled if its structural stop or external target trades before
confirmation.  No target, risk or exit rule is fitted to the result.
"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from dataclasses import asdict, replace
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

from adaptive_aggtrade_clock import build_daily_cost_resolved_bars  # noqa: E402
from aggtrade_data import download_aggtrade_days  # noqa: E402
from core import Side  # noqa: E402
from data import parse_utc_date  # noqa: E402
from directional_change_failed_sweep_week import (  # noqa: E402
    DirectionalChangeEvent,
    MAXIMUM_HOLD_NS,
    RETEST_WINDOW_MINUTES,
)
from impact_regime_probe import EventFeature, ImpactRegimeDetector, ScenarioPlan, simulate  # noqa: E402
from intrinsic_external_liquidity_v2_daily_week import (  # noqa: E402
    CLOCK_SOURCE_EXTRA_DAYS,
    DAILY_CANDIDATE_MINUTES,
    ROUND_TRIP_COST_BPS,
)
from intrinsic_external_liquidity_v2_week import (  # noqa: E402
    CONTEXT_WARMUP_DAYS,
    RoutingDecision,
    TargetFreeSweepRetestDetector,
    outer_state_series,
)
from intrinsic_external_liquidity_v3_router import (  # noqa: E402
    build_open_liquidity_snapshots,
    route_signal_indexed,
)


Mode = Literal["retest-bar-break", "new-40bp-directional-change"]
CONFIRMATION_WINDOW_NS = RETEST_WINDOW_MINUTES * 60 * 1_000_000_000


def atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)


def stop_or_target_touched(plan: ScenarioPlan, feature: EventFeature) -> str | None:
    bar = feature.bar
    if plan.side is Side.LONG:
        stop = bar.low <= plan.stop_price
        target = bar.high >= plan.target_price
    else:
        stop = bar.high >= plan.stop_price
        target = bar.low <= plan.target_price
    if stop:
        return "STOP_BEFORE_CONFIRMATION"
    if target:
        return "TARGET_CONSUMED_BEFORE_CONFIRMATION"
    return None


def derive_confirmed_plans(
    *,
    mode: Mode,
    features: list[EventFeature],
    base_plans: list[ScenarioPlan],
    directional_events: list[DirectionalChangeEvent],
) -> tuple[list[ScenarioPlan], dict[str, int]]:
    events_by_confirmation: dict[int, list[DirectionalChangeEvent]] = defaultdict(list)
    for event in directional_events:
        events_by_confirmation[int(event.confirmation_index)].append(event)

    derived: list[ScenarioPlan] = []
    counts: Counter[str] = Counter()
    for plan in base_plans:
        signal_index = plan.signal_bar_index
        signal_bar = features[signal_index].bar
        expiry_ns = plan.signal_time_ns + CONFIRMATION_WINDOW_NS
        trigger = signal_bar.high if plan.side is Side.LONG else signal_bar.low
        confirmed_index: int | None = None
        confirmation_hold = plan.confirmation_hold_price
        path_high = plan.pulse_high
        path_low = plan.pulse_low

        for index in range(signal_index + 1, len(features)):
            feature = features[index]
            bar = feature.bar
            if bar.end_time_ns > expiry_ns:
                counts["CONFIRMATION_WINDOW_EXPIRED"] += 1
                break
            path_high = max(path_high, bar.high)
            path_low = min(path_low, bar.low)
            invalidated = stop_or_target_touched(plan, feature)
            if invalidated is not None:
                counts[invalidated] += 1
                break

            if mode == "retest-bar-break":
                aligned_flow = (
                    feature.imbalance_z is not None
                    and plan.side.sign * feature.imbalance_z > 0.0
                )
                broken = (
                    bar.close > trigger
                    if plan.side is Side.LONG
                    else bar.close < trigger
                )
                if aligned_flow and broken:
                    confirmed_index = index
                    confirmation_hold = trigger
            else:
                required_type = "UP" if plan.side is Side.LONG else "DOWN"
                event_confirmed = any(
                    event.event_type == required_type
                    and event.pivot_index >= signal_index
                    for event in events_by_confirmation.get(index, ())
                )
                if event_confirmed:
                    confirmed_index = index
                    confirmation_hold = plan.confirmation_hold_price

            if confirmed_index is not None:
                counts["CONFIRMED"] += 1
                derived.append(
                    replace(
                        plan,
                        scenario_id=(
                            plan.scenario_id
                            + f":{mode}:{confirmed_index}"
                        ),
                        signal_bar_index=confirmed_index,
                        signal_time_ns=features[confirmed_index].bar.end_time_ns,
                        confirmation_hold_price=confirmation_hold,
                        pulse_high=path_high,
                        pulse_low=path_low,
                        reason_code=(
                            "RETEST_REJECTION_EXTREME_BROKEN"
                            if mode == "retest-bar-break"
                            else "FRESH_COST_RESOLVED_DIRECTIONAL_CHANGE_CONFIRMED"
                        ),
                    ),
                )
                break
        else:
            counts["FEATURE_STREAM_ENDED"] += 1
    return derived, dict(counts)


def run(args: argparse.Namespace) -> int:
    raw = json.loads(args.config.read_text(encoding="utf-8"))
    execution = dict(raw["execution"])
    evaluation_start = parse_utc_date(args.week)
    evaluation_end = evaluation_start + timedelta(days=7)
    context_start = evaluation_start - timedelta(days=CONTEXT_WARMUP_DAYS)
    clock_source_start = context_start - timedelta(days=CLOCK_SOURCE_EXTRA_DAYS)
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
        bar_start=context_start,
        bar_end=evaluation_end,
        minimum_range_bps=ROUND_TRIP_COST_BPS,
        candidate_minutes=DAILY_CANDIDATE_MINUTES,
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
    snapshots = build_open_liquidity_snapshots(
        features=features,
        events=detector.detector.events,
        signal_indices=(signal.signal_bar_index for signal in evaluation_signals),
    )

    base_plans: list[ScenarioPlan] = []
    routing_decisions: list[RoutingDecision] = []
    for signal in evaluation_signals:
        plan, decision = route_signal_indexed(
            signal=signal,
            features=features,
            end_times=end_times,
            outer_states=outer_states,
            events=detector.detector.events,
            snapshot=snapshots[signal.signal_bar_index],
            cost=cost,
            minimum_price_risk_fraction=minimum_price_fraction,
            minimum_net_reward_risk=minimum_net_rr,
        )
        routing_decisions.append(decision)
        if plan is not None:
            base_plans.append(plan)

    variants: dict[str, tuple[list[ScenarioPlan], dict[str, int]]] = {
        "baseline": (base_plans, {"BASE_ROUTED": len(base_plans)}),
    }
    for mode in ("retest-bar-break", "new-40bp-directional-change"):
        variants[mode] = derive_confirmed_plans(
            mode=mode,
            features=features,
            base_plans=base_plans,
            directional_events=detector.detector.events,
        )

    output = args.output
    output.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(asdict(row) for row in routing_decisions).to_csv(
        output / "routing_decisions.csv",
        index=False,
    )
    results: dict[str, Any] = {}
    for label, (plans, transition_counts) in variants.items():
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
        destination = output / label
        destination.mkdir(parents=True, exist_ok=True)
        trades.to_csv(destination / "trades.csv", index=False)
        daily.to_csv(destination / "daily_nav.csv", index=False)
        rejections.to_csv(destination / "rejections.csv", index=False)
        pd.DataFrame(asdict(row) for row in plans).to_csv(
            destination / "plans.csv",
            index=False,
        )
        atomic_json(destination / "metrics.json", metrics)
        results[label] = {
            "transition_counts": transition_counts,
            "metrics": metrics,
        }

    payload = {
        "diagnosis": "post-retest structural confirmation",
        "evaluation_start_utc": evaluation_start.isoformat(),
        "evaluation_end_utc": evaluation_end.isoformat(),
        "context_start_utc": context_start.isoformat(),
        "daily_candidate_minutes": list(DAILY_CANDIDATE_MINUTES),
        "round_trip_cost_bps": ROUND_TRIP_COST_BPS,
        "confirmation_window_minutes": RETEST_WINDOW_MINUTES,
        "base_routed_plans": len(base_plans),
        "results": results,
        "downloads": [record.to_dict() for record in records],
        "long_evaluation_run": False,
    }
    atomic_json(output / "confirmation_diagnostics_summary.json", payload)
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--week", required=True)
    parser.add_argument("--config", type=Path, default=HERE / "config.json")
    parser.add_argument(
        "--cache",
        type=Path,
        default=ROOT / ".cache" / "candidate-01-intrinsic-confirmation-diagnostics",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "artifacts" / "candidate-01-intrinsic-confirmation-diagnostics",
    )
    parser.add_argument("--workers", type=int, default=4)
    return parser


if __name__ == "__main__":
    raise SystemExit(run(build_parser().parse_args()))
