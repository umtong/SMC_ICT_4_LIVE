"""Compare source half-profit/breakeven management against full-target exits."""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import date, timedelta
import json
from pathlib import Path

import pandas as pd

from backtest_support import make_engine, write_json
from fee_profiles_v5 import FEE_PROFILES, make_instrument_with_fee_profile
from instruments import CONTRACTS
from mtf_backtest_support_day_v7 import preserve_daytrade_results
from mtf_data import add_symbol_mtf_data
import mtf_strategy as _base_strategy
from mtf_strategy import EasyChartMTFConfig
from mtf_strategy_daily_risk_v11 import (
    DAILY_BOUNDARY_TRANSLATION,
    DAILY_LOSS_CAP_FRACTION,
    DAILY_RISK_PROVENANCE,
    DailyRiskDayTradeStrategy,
)
from mtf_strategy_half_be_v12 import (
    HALF_BE_PROVENANCE,
    RUNNER_EXIT_PROVENANCE,
    HalfThenBreakevenStrategy,
)
from robustness_v8 import trade_robustness_metrics
from scenario_detached_retest_v8 import (
    CLOSE_DETACHED_RETEST_RULE,
    MicroCloseDetachedRetestBundleV9,
)


MANAGEMENT_POLICIES = {
    "full_target": (
        DailyRiskDayTradeStrategy,
        "CONTROL:FULL_POSITION_AT_FIRST_OPPOSING_STRUCTURE",
    ),
    "half_be_runner": (
        HalfThenBreakevenStrategy,
        HALF_BE_PROVENANCE,
    ),
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", type=date.fromisoformat, required=True)
    parser.add_argument("--end", type=date.fromisoformat, required=True)
    parser.add_argument("--warmup-days", type=int, default=30)
    parser.add_argument("--symbols", nargs="+", default=list(CONTRACTS))
    parser.add_argument("--cache", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--fee-profile", choices=tuple(FEE_PROFILES), required=True)
    parser.add_argument("--management-policy", choices=tuple(MANAGEMENT_POLICIES), required=True)
    parser.add_argument("--min-gross-rr", type=float, default=1.0)
    parser.add_argument("--entry-slippage-ticks", type=int, default=2)
    parser.add_argument("--stop-slippage-ticks", type=int, default=2)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.end < args.start:
        raise SystemExit("--end must be >= --start")
    if args.warmup_days < 1:
        raise SystemExit("--warmup-days must be positive")
    symbols = tuple(args.symbols)
    unknown = sorted(set(symbols) - set(CONTRACTS))
    if unknown:
        raise SystemExit(f"unknown symbols: {unknown}")
    args.output.mkdir(parents=True, exist_ok=True)
    args.cache.mkdir(parents=True, exist_ok=True)

    profile = FEE_PROFILES[args.fee_profile]
    strategy_type, management_provenance = MANAGEMENT_POLICIES[args.management_policy]
    _base_strategy.MultiScaleScenarioBundle = MicroCloseDetachedRetestBundleV9

    engine = make_engine()
    instruments = [make_instrument_with_fee_profile(symbol, profile) for symbol in symbols]
    source_types = []
    trigger_types = []
    decision_types = []
    higher_types = []
    load_start = args.start - timedelta(days=args.warmup_days)
    for symbol, instrument in zip(symbols, instruments, strict=True):
        engine.add_instrument(instrument)
        source_type, trigger_type, decision_type, higher_type = add_symbol_mtf_data(
            engine,
            symbol,
            instrument,
            load_start,
            args.end,
            args.cache,
        )
        source_types.append(source_type)
        trigger_types.append(trigger_type)
        decision_types.append(decision_type)
        higher_types.append(higher_type)
    engine.sort_data()

    strategy = strategy_type(
        EasyChartMTFConfig(
            instrument_ids=tuple(item.id for item in instruments),
            higher_bar_types=tuple(higher_types),
            decision_bar_types=tuple(decision_types),
            trigger_bar_types=tuple(trigger_types),
            execution_bar_types=tuple(source_types),
            risk_fraction=float(DAILY_LOSS_CAP_FRACTION),
            min_gross_rr=args.min_gross_rr,
            estimated_entry_fee_rate=float(profile.taker_rate),
            estimated_stop_fee_rate=float(profile.taker_rate),
            estimated_entry_slippage_ticks=args.entry_slippage_ticks,
            estimated_stop_slippage_ticks=args.stop_slippage_ticks,
            trading_start_ns=int(pd.Timestamp(args.start, tz="UTC").value),
        ),
    )
    engine.add_strategy(strategy)
    try:
        engine.run()
        metrics = preserve_daytrade_results(
            engine,
            strategy,
            args.output,
            symbols=symbols,
            start=args.start,
            end=args.end,
        )
        trade_audit = pd.read_csv(args.output / "trade_audit.csv")
        robustness = trade_robustness_metrics(
            trade_audit,
            reported_total_return=metrics.get("total_return"),
        )
        event_counts = Counter(event.get("kind") for event in strategy.event_log)
        metrics.update(
            {
                "candidate": "candidate-easychart-v12-half-be",
                "scale_policy": "micro_only",
                "target_policy": "nearest_any_confirmed_preexisting_opposite_pivot",
                "retest_policy": "close_detached",
                "retest_policy_provenance": CLOSE_DETACHED_RETEST_RULE,
                "management_policy": args.management_policy,
                "management_policy_provenance": management_provenance,
                "runner_exit_provenance": (
                    RUNNER_EXIT_PROVENANCE
                    if args.management_policy == "half_be_runner"
                    else None
                ),
                "first_target_resize_requests": int(
                    event_counts["first_target_resize_requested"],
                ),
                "first_half_targets_completed": int(
                    event_counts["first_half_target_completed"],
                ),
                "breakeven_runner_stops_submitted": int(
                    event_counts["breakeven_runner_stop_submitted"],
                ),
                "breakeven_runner_stops_filled": int(
                    event_counts["breakeven_runner_stop_filled"],
                ),
                "plans_rejected_unsplittable_quantity": int(
                    event_counts["plan_rejected_unsplittable_quantity"],
                ),
                "plans_rejected_partial_leg_below_minimum": int(
                    event_counts["plan_rejected_partial_leg_below_minimum"],
                ),
                "daily_risk_policy": "one_percent_starting_nav_gross_loss_budget",
                "daily_loss_cap_fraction": float(DAILY_LOSS_CAP_FRACTION),
                "daily_risk_provenance": DAILY_RISK_PROVENANCE,
                "daily_boundary_translation": DAILY_BOUNDARY_TRANSLATION,
                "daily_risk_sessions": int(event_counts["daily_risk_session_started"]),
                "plans_rejected_daily_loss_cap": int(
                    event_counts["plan_rejected_daily_loss_cap"],
                ),
                "fee_profile": profile.name,
                "maker_fee_rate": float(profile.maker_rate),
                "taker_fee_rate": float(profile.taker_rate),
                "funding_accounting": "NOT_YET_APPLIED_NATIVE_ENGINE_SMOKE_UNRESOLVED",
                "diagnostic_only": True,
                **robustness,
            },
        )
        write_json(args.output / "metrics.json", metrics)

        run_path = args.output / "run.json"
        run_record = json.loads(run_path.read_text(encoding="utf-8"))
        run_record.update(
            {
                "candidate": "candidate-easychart-v12-half-be",
                "retest_policy": "close_detached",
                "management_policy": args.management_policy,
                "management_policy_provenance": management_provenance,
                "runner_exit_provenance": (
                    RUNNER_EXIT_PROVENANCE
                    if args.management_policy == "half_be_runner"
                    else None
                ),
                "daily_risk_policy": "one_percent_starting_nav_gross_loss_budget",
                "daily_risk_provenance": DAILY_RISK_PROVENANCE,
                "funding_accounting": "NOT_YET_APPLIED_NATIVE_ENGINE_SMOKE_UNRESOLVED",
                "diagnostic_only": True,
            },
        )
        write_json(run_path, run_record)
        print(json.dumps(metrics, ensure_ascii=False, indent=2, sort_keys=True))
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
