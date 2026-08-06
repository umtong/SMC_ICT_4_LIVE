#!/usr/bin/env python3
"""Hierarchical external-liquidity auction portfolio on Nautilus TradeTicks.

The candidate separates a local sweep detector from the scenario which is
allowed to trade it.

Reversal response
-----------------
A cost-resolved intrinsic failed sweep may reverse only when the completed
signal is in the correct half of the trailing 24-hour dealing range, in the
outer quartile of the trailing 72-hour dealing range, and not fighting strong
72-hour delivery without a 160-bps outer change of character.  The target is
the nearest already-confirmed, still-unconsumed opposing intrinsic liquidity
pool whose cost-net geometry is valid.

Continuation responses
----------------------
A failed reversal which subsequently accepts outside value is a different
state, not a looser reversal.  It projects the accepted excursion.  Direct
outside-value retention with a holding boundary retest is the second,
mutually-exclusive continuation state.

Signals and plans are candidate logic.  Fills, stops, targets, fees, margin,
positions and NAV are produced exclusively by NautilusTrader 1.230.0 from
official Binance Vision USD-M aggregate trades represented one-for-one as
TradeTick objects.
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
from aggtrade_data import AggTrade, download_aggtrade_days, iter_downloads  # noqa: E402
from data import parse_utc_date  # noqa: E402
from directional_change_failed_sweep_week import MAXIMUM_HOLD_NS  # noqa: E402
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
from intrinsic_external_liquidity_v3_router import (  # noqa: E402
    build_open_liquidity_snapshots,
    route_signal_indexed,
)
from intrinsic_failed_reversal_continuation_v8_nautilus_week import (  # noqa: E402
    build_confirmations,
)
from intrinsic_full_measured_v11_nautilus_week import direct_measured_plans  # noqa: E402
from intrinsic_measured_acceptance_v10_nautilus_week import measured_plans  # noqa: E402
from intrinsic_outside_acceptance_v9_nautilus_week import (  # noqa: E402
    OutsideValueAcceptanceDetector,
)
from nautilus_plan_backtest import NautilusExecutionConfig  # noqa: E402
from nautilus_tick_plan_backtest import run_nautilus_tick_plan_backtest  # noqa: E402

RULES = (
    "hierarchical-reversal-only",
    "hierarchical-full-composite",
)
FLUSH_TICKS = 3


def atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def load_execution(path: Path) -> NautilusExecutionConfig:
    config = NautilusExecutionConfig.from_mapping(
        json.loads(path.read_text(encoding="utf-8")),
    )
    if abs(config.risk_fraction - 0.03) > 1e-12:
        raise ValueError("authoritative candidate-01 evaluations require 3% risk")
    return config


def execution_trade_windows(
    records: list[Any],
    *,
    plans: list[ScenarioPlan],
    start_ns: int,
    end_ns: int,
    maximum_hold_ns: int,
) -> tuple[list[AggTrade], list[tuple[int, int]]]:
    """Select causal plan windows and one official marker trade per UTC day."""

    padding_before = 60 * 1_000_000_000
    padding_after = 120 * 1_000_000_000
    ns_per_day = 86_400 * 1_000_000_000
    intervals = sorted(
        (
            max(start_ns, int(plan.signal_time_ns) - padding_before),
            min(
                end_ns - 1,
                int(plan.signal_time_ns) + maximum_hold_ns + padding_after,
            ),
        )
        for plan in plans
        if start_ns <= int(plan.signal_time_ns) < end_ns
    )
    merged: list[tuple[int, int]] = []
    for left, right in intervals:
        if right < left:
            continue
        if not merged or left > merged[-1][1] + 1:
            merged.append((left, right))
        else:
            merged[-1] = (merged[-1][0], max(merged[-1][1], right))

    result: list[AggTrade] = []
    interval_index = 0
    flush = 0
    last_evaluation_day: int | None = None
    evaluation_days_seen: set[int] = set()
    for trade in iter_downloads(records):
        ts_ns = int(trade.ts_event_ns)
        if ts_ns < start_ns:
            continue
        if ts_ns >= end_ns:
            if flush < FLUSH_TICKS:
                result.append(trade)
                flush += 1
                continue
            break

        day_id = ts_ns // ns_per_day
        if day_id != last_evaluation_day:
            last_evaluation_day = day_id
            evaluation_days_seen.add(day_id)
            result.append(trade)
            continue

        while interval_index < len(merged) and ts_ns > merged[interval_index][1]:
            interval_index += 1
        if (
            interval_index < len(merged)
            and merged[interval_index][0] <= ts_ns <= merged[interval_index][1]
        ):
            result.append(trade)

    expected_days = (end_ns - start_ns) // ns_per_day
    if len(evaluation_days_seen) != expected_days:
        raise RuntimeError(
            f"expected {expected_days} evaluation day markers, "
            f"found {len(evaluation_days_seen)}",
        )
    if flush < FLUSH_TICKS:
        raise RuntimeError(
            f"expected {FLUSH_TICKS} post-evaluation trades, found {flush}",
        )
    return result, merged


def run(args: argparse.Namespace) -> int:
    execution = load_execution(args.execution_config)
    evaluation_start = parse_utc_date(args.week)
    evaluation_end = evaluation_start + timedelta(days=7)
    context_start = evaluation_start - timedelta(days=CONTEXT_WARMUP_DAYS)
    clock_source_start = context_start - timedelta(days=CLOCK_SOURCE_EXTRA_DAYS)
    execution_download_end = evaluation_end + timedelta(minutes=1)
    start_ns = int(pd.Timestamp(evaluation_start).as_unit("ns").value)
    end_ns = int(pd.Timestamp(evaluation_end).as_unit("ns").value)

    records = download_aggtrade_days(
        symbol="BTCUSDT",
        start=clock_source_start,
        end=execution_download_end,
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
    failed_detector = TargetFreeSweepRetestDetector()
    acceptance_detector = OutsideValueAcceptanceDetector()
    for bar in bars:
        feature_detector.on_bar(bar)
        index = len(feature_detector.features) - 1
        failed_detector.on_feature(index=index, features=feature_detector.features)
        acceptance_detector.on_feature(index=index, features=feature_detector.features)

    features = feature_detector.features
    end_times = [row.bar.end_time_ns for row in features]
    outer_states = outer_state_series(features)
    failed_signals = [
        signal
        for signal in failed_detector.signals
        if start_ns <= signal.signal_time_ns < end_ns
    ]
    acceptance_signals = [
        signal
        for signal in acceptance_detector.signals
        if start_ns <= signal.signal_time_ns < end_ns
    ]
    cost = execution.all_in_cost_bps_per_side / 10_000.0

    failed_snapshots = build_open_liquidity_snapshots(
        features=features,
        events=failed_detector.detector.events,
        signal_indices=(signal.signal_bar_index for signal in failed_signals),
    )

    reversal_plans: list[ScenarioPlan] = []
    reversal_decisions: list[dict[str, Any]] = []
    for signal in failed_signals:
        routed, decision = route_signal_indexed(
            signal=signal,
            features=features,
            end_times=end_times,
            outer_states=outer_states,
            events=failed_detector.detector.events,
            snapshot=failed_snapshots[signal.signal_bar_index],
            cost=cost,
            minimum_price_risk_fraction=execution.minimum_price_risk_fraction,
            minimum_net_reward_risk=execution.minimum_net_reward_risk,
        )
        reversal_decisions.append(asdict(decision))
        if routed is not None:
            reversal_plans.append(routed)

    confirmations: list[Any] = []
    continuation_setup_decisions: list[dict[str, Any]] = []
    failed_measured_plans: list[ScenarioPlan] = []
    failed_measured_decisions: list[dict[str, Any]] = []
    direct_measured: list[ScenarioPlan] = []
    direct_decisions: list[dict[str, Any]] = []
    if args.rule == "hierarchical-full-composite":
        confirmations, continuation_setup_decisions = build_confirmations(
            signals=failed_signals,
            features=features,
            outer_states=outer_states,
            events=failed_detector.detector.events,
            signal_snapshots=failed_snapshots,
            cost=cost,
            minimum_price_risk_fraction=execution.minimum_price_risk_fraction,
            minimum_net_reward_risk=execution.minimum_net_reward_risk,
            evaluation_end_ns=end_ns,
        )
        failed_measured_plans, failed_measured_decisions = measured_plans(
            confirmations,
            features,
        )
        direct_measured, direct_decisions = direct_measured_plans(
            acceptance_signals,
            features,
            outer_states,
        )

    plans = list(reversal_plans)
    plans.extend(failed_measured_plans)
    plans.extend(direct_measured)
    execution_trades, execution_windows = execution_trade_windows(
        records,
        plans=plans,
        start_ns=start_ns,
        end_ns=end_ns,
        maximum_hold_ns=MAXIMUM_HOLD_NS,
    )

    output = args.output
    output.mkdir(parents=True, exist_ok=True)
    evidence = run_nautilus_tick_plan_backtest(
        label=f"BTCUSDT-{args.rule}-{evaluation_start.date().isoformat()}-7d",
        trades=execution_trades,
        plans=plans,
        evaluation_start=evaluation_start,
        evaluation_end=evaluation_end,
        execution=execution,
        maximum_hold_ns=MAXIMUM_HOLD_NS,
        output_dir=output,
    )

    pd.DataFrame(asdict(row) for row in failed_signals).to_csv(
        output / "failed_retest_signals.csv", index=False,
    )
    pd.DataFrame(asdict(row) for row in acceptance_signals).to_csv(
        output / "acceptance_signals.csv", index=False,
    )
    pd.DataFrame(reversal_decisions).to_csv(
        output / "reversal_decisions.csv", index=False,
    )
    pd.DataFrame(asdict(row) for row in confirmations).to_csv(
        output / "failed_reversal_confirmations.csv", index=False,
    )
    pd.DataFrame(continuation_setup_decisions).to_csv(
        output / "continuation_setup_decisions.csv", index=False,
    )
    pd.DataFrame(failed_measured_decisions).to_csv(
        output / "failed_measured_decisions.csv", index=False,
    )
    pd.DataFrame(direct_decisions).to_csv(
        output / "direct_measured_decisions.csv", index=False,
    )
    pd.DataFrame(item.to_dict() for item in calibrations).to_json(
        output / "daily_clock_calibrations.json", orient="records", indent=2,
    )

    payload = {
        "candidate": "hierarchical external-liquidity auction portfolio",
        "rule": args.rule,
        "authoritative_backtest": True,
        "execution_engine": "NautilusTrader",
        "execution_data_type": "TradeTick",
        "custom_fill_simulator": False,
        "custom_pnl_or_nav_ledger": False,
        "evaluation_start_utc": evaluation_start.isoformat(),
        "evaluation_end_utc": evaluation_end.isoformat(),
        "failed_retest_signals": len(failed_signals),
        "acceptance_signals": len(acceptance_signals),
        "hierarchical_reversal_plans": len(reversal_plans),
        "failed_reversal_confirmations": len(confirmations),
        "failed_reversal_measured_plans": len(failed_measured_plans),
        "direct_measured_plans": len(direct_measured),
        "submitted_plan_pool": len(plans),
        "official_execution_trade_ticks": len(execution_trades),
        "tick_selection": (
            "outcome-independent plan windows plus the first official trade "
            "of each evaluation UTC day for NAV marking"
        ),
        "execution_tick_windows": [list(row) for row in execution_windows],
        "reversal_decision_counts": dict(
            Counter(row["reason_code"] for row in reversal_decisions)
        ),
        "failed_measured_decision_counts": dict(
            Counter(row["reason_code"] for row in failed_measured_decisions)
        ),
        "direct_measured_decision_counts": dict(
            Counter(row["reason_code"] for row in direct_decisions)
        ),
        "risk_fraction": execution.risk_fraction,
        "all_in_cost_bps_per_side": execution.all_in_cost_bps_per_side,
        "maximum_hold_hours": MAXIMUM_HOLD_NS / NS_PER_HOUR,
        "metrics": evidence.metrics,
        "downloads": [record.to_dict() for record in records],
        "long_evaluation_run": False,
    }
    atomic_json(output / "intrinsic_hierarchical_auction_v16_summary.json", payload)
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--week", required=True)
    parser.add_argument("--rule", required=True, choices=RULES)
    parser.add_argument(
        "--execution-config", type=Path,
        default=HERE / "nautilus_execution.json",
    )
    parser.add_argument(
        "--cache", type=Path,
        default=ROOT / ".cache" / "candidate-01-v16-hierarchical-auction",
    )
    parser.add_argument(
        "--output", type=Path,
        default=ROOT / "artifacts" / "candidate-01-v16-hierarchical-auction",
    )
    parser.add_argument("--workers", type=int, default=4)
    return parser


if __name__ == "__main__":
    raise SystemExit(run(build_parser().parse_args()))
