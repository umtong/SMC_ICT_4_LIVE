#!/usr/bin/env python3
"""Controlled external-liquidity hierarchy diagnosis on one BTC week.

The current router checks that a completed retest signal lies in the outer
24h/72h dealing-range region.  Location alone does not prove that the failed
initiative actually removed external liquidity.  This diagnostic freezes every
other rule and adds one causal condition at a time:

``prior-24h-external-sweep``
    The failed-sweep path must trade beyond the completed trailing 24-hour high
    or low that existed immediately before the sweep-confirmation event, then
    the confirmation close must return inside that range.

``prior-72h-external-sweep``
    The identical condition at the completed trailing 72-hour boundary.

The lookback ends before setup creation, so neither the sweep nor its retest can
change the liquidity boundary being tested.  This is a liquidity-event test,
not a profitability-fitted distance threshold.
"""

from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import asdict
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
from directional_change_failed_sweep_week import MAXIMUM_HOLD_NS  # noqa: E402
from impact_regime_probe import ImpactRegimeDetector, ScenarioPlan, simulate  # noqa: E402
from intrinsic_external_liquidity_v2_daily_week import (  # noqa: E402
    CLOCK_SOURCE_EXTRA_DAYS,
    DAILY_CANDIDATE_MINUTES,
    ROUND_TRIP_COST_BPS,
)
from intrinsic_external_liquidity_v2_week import (  # noqa: E402
    CONTEXT_WARMUP_DAYS,
    RoutingDecision,
    TargetFreeSweepRetestDetector,
    completed_range,
    outer_state_series,
)
from intrinsic_external_liquidity_v3_router import (  # noqa: E402
    build_open_liquidity_snapshots,
    route_signal_indexed,
)


HIERARCHY_HOURS = (24, 72)


def atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)


def setup_created_index(plan: ScenarioPlan) -> int:
    parts = plan.scenario_id.split(":")
    if len(parts) < 2 or parts[0] != "dc-target-free":
        raise ValueError(f"cannot parse target-free scenario id: {plan.scenario_id}")
    return int(parts[1])


def actually_swept_completed_external_range(
    *,
    plan: ScenarioPlan,
    features,
    end_times: list[int],
    hours: int,
) -> tuple[bool, str]:
    created_index = setup_created_index(plan)
    if created_index <= 0:
        return False, "NO_PRE_SETUP_EVENT"
    prior = completed_range(
        features,
        end_times,
        end_index=created_index - 1,
        hours=hours,
    )
    if prior is None or prior.width <= 0.0:
        return False, "INCOMPLETE_PRE_SETUP_RANGE"
    confirmation_close = features[created_index].bar.close
    if plan.side is Side.LONG:
        swept = plan.pulse_low < prior.low
        reentered = confirmation_close > prior.low
    else:
        swept = plan.pulse_high > prior.high
        reentered = confirmation_close < prior.high
    if not swept:
        return False, "PRE_SETUP_EXTERNAL_LIQUIDITY_NOT_TOUCHED"
    if not reentered:
        return False, "OUTSIDE_VALUE_RETAINED_AT_PRE_SETUP_BOUNDARY"
    return True, "ACTUAL_COMPLETED_EXTERNAL_LIQUIDITY_SWEEP"


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
    bars, _ = build_daily_cost_resolved_bars(
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
    decisions: list[RoutingDecision] = []
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
        decisions.append(decision)
        if plan is not None:
            base_plans.append(plan)

    variants: dict[str, tuple[list[ScenarioPlan], dict[str, int]]] = {
        "baseline": (base_plans, {"BASE_ROUTED": len(base_plans)}),
    }
    evidence_rows: list[dict[str, Any]] = []
    for hours in HIERARCHY_HOURS:
        accepted: list[ScenarioPlan] = []
        counts: Counter[str] = Counter()
        for plan in base_plans:
            passed, reason = actually_swept_completed_external_range(
                plan=plan,
                features=features,
                end_times=end_times,
                hours=hours,
            )
            counts[reason] += 1
            evidence_rows.append(
                {
                    "scenario_id": plan.scenario_id,
                    "signal_time_ns": plan.signal_time_ns,
                    "side": plan.side.value,
                    "hierarchy_hours": hours,
                    "accepted": passed,
                    "reason_code": reason,
                },
            )
            if passed:
                accepted.append(plan)
        variants[f"prior-{hours}h-external-sweep"] = (accepted, dict(counts))

    output = args.output
    output.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(evidence_rows).to_csv(output / "hierarchy_evidence.csv", index=False)
    pd.DataFrame(asdict(row) for row in decisions).to_csv(
        output / "routing_decisions.csv",
        index=False,
    )
    results: dict[str, Any] = {}
    for label, (plans, hierarchy_counts) in variants.items():
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
        atomic_json(destination / "metrics.json", metrics)
        results[label] = {
            "hierarchy_counts": hierarchy_counts,
            "metrics": metrics,
        }

    payload = {
        "diagnosis": "actual completed external-liquidity sweep versus outer-range location",
        "evaluation_start_utc": evaluation_start.isoformat(),
        "evaluation_end_utc": evaluation_end.isoformat(),
        "base_routed_plans": len(base_plans),
        "hierarchy_hours": list(HIERARCHY_HOURS),
        "results": results,
        "downloads": [record.to_dict() for record in records],
        "long_evaluation_run": False,
    }
    atomic_json(output / "sweep_hierarchy_diagnostics_summary.json", payload)
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--week", required=True)
    parser.add_argument("--config", type=Path, default=HERE / "config.json")
    parser.add_argument(
        "--cache",
        type=Path,
        default=ROOT / ".cache" / "candidate-01-intrinsic-sweep-hierarchy",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "artifacts" / "candidate-01-intrinsic-sweep-hierarchy",
    )
    parser.add_argument("--workers", type=int, default=4)
    return parser


if __name__ == "__main__":
    raise SystemExit(run(build_parser().parse_args()))
