#!/usr/bin/env python3
"""Measured expansion after a countertrend failed-auction reversal fails.

V8 showed that causal continuation confirmations often occur during price
discovery, where no still-open historical pivot exists in the continuation
direction.  This control changes only the target and active invalidation after
that terminal state transition:

* the complete failed-reversal path extreme becomes the new acceptance boundary;
* invalidation sits one cost-resolvable 26-bps price-risk unit back through it;
* the accepted outside excursion from the old swept boundary to the new
  acceptance boundary is projected once in the continuation direction.

The projection is an auction measured move, not a PnL-fitted R multiple.  A
confirmation bar which already consumed the projection is rejected.  The same
Nautilus execution, 7-bps/side costs, 3% current-NAV risk and four-hour maximum
hold remain unchanged.
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
from data import load_interval, parse_utc_date  # noqa: E402
from directional_change_failed_sweep_week import (  # noqa: E402
    MAXIMUM_HOLD_NS,
    MINIMUM_COST_RESOLVABLE_RISK_BPS,
)
from impact_regime_probe import ImpactRegimeDetector, ScenarioPlan  # noqa: E402
from intrinsic_external_liquidity_v2_daily_week import (  # noqa: E402
    CLOCK_SOURCE_EXTRA_DAYS,
    DAILY_CANDIDATE_MINUTES,
    ROUND_TRIP_COST_BPS,
)
from intrinsic_external_liquidity_v2_week import (  # noqa: E402
    CONTEXT_WARMUP_DAYS,
    NS_PER_HOUR,
    TargetFreeSweepRetestDetector,
    outer_state_series,
)
from intrinsic_external_liquidity_v3_router import build_open_liquidity_snapshots  # noqa: E402
from intrinsic_failed_reversal_continuation_v8_nautilus_week import (  # noqa: E402
    build_aligned_reversal_plans,
    build_confirmations,
)
from nautilus_plan_backtest import (  # noqa: E402
    NautilusExecutionConfig,
    run_nautilus_plan_backtest,
)

RULES = ("measured-continuation-only", "measured-composite")
MINIMUM_PRICE_RISK_FRACTION = MINIMUM_COST_RESOLVABLE_RISK_BPS / 10_000.0


def atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)


def load_execution(path: Path) -> NautilusExecutionConfig:
    config = NautilusExecutionConfig.from_mapping(
        json.loads(path.read_text(encoding="utf-8")),
    )
    if abs(config.risk_fraction - 0.03) > 1e-12:
        raise ValueError("authoritative candidate-01 evaluations require 3% risk")
    return config


def measured_plans(
    confirmations: list[Any],
    features: list[Any],
) -> tuple[list[ScenarioPlan], list[dict[str, Any]]]:
    plans: list[ScenarioPlan] = []
    decisions: list[dict[str, Any]] = []
    for row in confirmations:
        side = Side.LONG if row.continuation_side == "LONG" else Side.SHORT
        width = abs(row.break_level - row.invalidation_boundary)
        if width <= 0.0:
            decisions.append({**asdict(row), "routed": False, "reason_code": "ZERO_ACCEPTED_EXCURSION"})
            continue
        if side is Side.LONG:
            stop = row.break_level * (1.0 - MINIMUM_PRICE_RISK_FRACTION)
            target = row.break_level + width
            target_consumed = row.observed_path_high >= target
            geometry = stop < row.confirmation_close < target
        else:
            stop = row.break_level * (1.0 + MINIMUM_PRICE_RISK_FRACTION)
            target = row.break_level - width
            target_consumed = row.observed_path_low <= target
            geometry = target < row.confirmation_close < stop
        if target_consumed:
            decisions.append(
                {**asdict(row), "routed": False, "reason_code": "MEASURED_EXPANSION_ALREADY_CONSUMED"},
            )
            continue
        if not geometry:
            decisions.append(
                {**asdict(row), "routed": False, "reason_code": "INVALID_MEASURED_EXPANSION_GEOMETRY"},
            )
            continue
        plan = ScenarioPlan(
            scenario_id=(
                f"measured-failed-reversal:{row.source_signal_index}:"
                f"{side.value.lower()}:{row.confirmation_time_ns}"
            ),
            response="FAILED_REVERSAL_MEASURED_CONTINUATION",
            side=side,
            signal_bar_index=row.confirmation_index,
            signal_time_ns=row.confirmation_time_ns,
            stop_price=stop,
            target_price=target,
            confirmation_hold_price=row.break_level,
            structure_high=max(row.observed_path_high, stop, target),
            structure_low=min(row.observed_path_low, stop, target),
            structure_midpoint=0.5 * (row.break_level + target),
            pulse_high=row.observed_path_high,
            pulse_low=row.observed_path_low,
            pulse_flow_score=row.confirmation_imbalance_z,
            pulse_move_atr=0.0,
            pulse_path_efficiency=0.0,
            pulse_close_location=0.0,
            reason_code="FAILED_REVERSAL_ACCEPTED_EXCURSION_MEASURED_EXPANSION",
        )
        plans.append(plan)
        decisions.append(
            {
                **asdict(row),
                "routed": True,
                "reason_code": plan.reason_code,
                "measured_width": width,
                "selected_stop": stop,
                "selected_target": target,
            },
        )
    return plans, decisions


def run(args: argparse.Namespace) -> int:
    execution = load_execution(args.execution_config)
    evaluation_start = parse_utc_date(args.week)
    evaluation_end = evaluation_start + timedelta(days=7)
    context_start = evaluation_start - timedelta(days=CONTEXT_WARMUP_DAYS)
    clock_source_start = context_start - timedelta(days=CLOCK_SOURCE_EXTRA_DAYS)
    start_ns = int(pd.Timestamp(evaluation_start).as_unit("ns").value)
    end_ns = int(pd.Timestamp(evaluation_end).as_unit("ns").value)

    signal_records = download_aggtrade_days(
        symbol="BTCUSDT",
        start=clock_source_start,
        end=evaluation_end,
        cache_dir=args.cache,
        workers=args.workers,
    )
    bars, calibrations = build_daily_cost_resolved_bars(
        signal_records,
        bar_start=context_start,
        bar_end=evaluation_end,
        minimum_range_bps=ROUND_TRIP_COST_BPS,
        candidate_minutes=DAILY_CANDIDATE_MINUTES,
    )
    execution_frame, execution_records = load_interval(
        symbol="BTCUSDT",
        start=evaluation_start,
        end=evaluation_end,
        cache_dir=args.cache / "execution-klines",
        warmup_minutes=2,
    )

    feature_detector = ImpactRegimeDetector()
    detector = TargetFreeSweepRetestDetector()
    for bar in bars:
        feature_detector.on_bar(bar)
        index = len(feature_detector.features) - 1
        detector.on_feature(index=index, features=feature_detector.features)
    features = feature_detector.features
    outer_states = outer_state_series(features)
    signals = [
        signal for signal in detector.signals
        if start_ns <= signal.signal_time_ns < end_ns
    ]
    cost = execution.all_in_cost_bps_per_side / 10_000.0
    snapshots = build_open_liquidity_snapshots(
        features=features,
        events=detector.detector.events,
        signal_indices=(signal.signal_bar_index for signal in signals),
    )

    confirmations, setup_decisions = build_confirmations(
        signals=signals,
        features=features,
        outer_states=outer_states,
        events=detector.detector.events,
        signal_snapshots=snapshots,
        cost=cost,
        minimum_price_risk_fraction=execution.minimum_price_risk_fraction,
        minimum_net_reward_risk=execution.minimum_net_reward_risk,
        evaluation_end_ns=end_ns,
    )
    continuation_plans, measured_decisions = measured_plans(confirmations, features)
    reversal_plans, reversal_decisions = build_aligned_reversal_plans(
        signals=signals,
        features=features,
        outer_states=outer_states,
        events=detector.detector.events,
        snapshots=snapshots,
        cost=cost,
        minimum_price_risk_fraction=execution.minimum_price_risk_fraction,
        minimum_net_reward_risk=execution.minimum_net_reward_risk,
    )

    plans = list(continuation_plans)
    if args.rule == "measured-composite":
        plans.extend(reversal_plans)

    output = args.output
    output.mkdir(parents=True, exist_ok=True)
    evidence = run_nautilus_plan_backtest(
        label=f"BTCUSDT-{args.rule}-{evaluation_start.date().isoformat()}-7d",
        features=features,
        execution_frame=execution_frame,
        plans=plans,
        evaluation_start=evaluation_start,
        evaluation_end=evaluation_end,
        execution=execution,
        maximum_hold_ns=MAXIMUM_HOLD_NS,
        output_dir=output,
    )

    pd.DataFrame(asdict(row) for row in confirmations).to_csv(
        output / "continuation_confirmations.csv", index=False,
    )
    pd.DataFrame(setup_decisions).to_csv(output / "continuation_setup_decisions.csv", index=False)
    pd.DataFrame(measured_decisions).to_csv(output / "measured_routing_decisions.csv", index=False)
    pd.DataFrame(reversal_decisions).to_csv(output / "reversal_routing_decisions.csv", index=False)
    pd.DataFrame(item.to_dict() for item in calibrations).to_json(
        output / "daily_clock_calibrations.json", orient="records", indent=2,
    )

    payload = {
        "candidate": "measured outside-value expansion after failed reversal",
        "rule": args.rule,
        "authoritative_backtest": True,
        "execution_engine": "NautilusTrader",
        "custom_fill_simulator": False,
        "custom_pnl_or_nav_ledger": False,
        "execution_market_data": "official Binance Vision USD-M one-minute klines",
        "evaluation_start_utc": evaluation_start.isoformat(),
        "evaluation_end_utc": evaluation_end.isoformat(),
        "continuation_confirmations": len(confirmations),
        "measured_continuation_plans": len(continuation_plans),
        "aligned_flow_takeover_reversal_plans": len(reversal_plans),
        "submitted_plan_pool": len(plans),
        "measured_routing_counts": dict(Counter(row["reason_code"] for row in measured_decisions)),
        "risk_fraction": execution.risk_fraction,
        "all_in_cost_bps_per_side": execution.all_in_cost_bps_per_side,
        "minimum_cost_resolvable_price_risk_bps": MINIMUM_COST_RESOLVABLE_RISK_BPS,
        "maximum_hold_hours": MAXIMUM_HOLD_NS / NS_PER_HOUR,
        "metrics": evidence.metrics,
        "signal_downloads": [record.to_dict() for record in signal_records],
        "execution_downloads": [asdict(record) for record in execution_records],
        "long_evaluation_run": False,
    }
    atomic_json(output / "intrinsic_measured_acceptance_v10_summary.json", payload)
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--week", required=True)
    parser.add_argument("--rule", required=True, choices=RULES)
    parser.add_argument(
        "--execution-config", type=Path, default=HERE / "nautilus_execution.json",
    )
    parser.add_argument(
        "--cache", type=Path, default=ROOT / ".cache" / "candidate-01-v10-measured",
    )
    parser.add_argument(
        "--output", type=Path, default=ROOT / "artifacts" / "candidate-01-v10-measured",
    )
    parser.add_argument("--workers", type=int, default=4)
    return parser


if __name__ == "__main__":
    raise SystemExit(run(build_parser().parse_args()))
