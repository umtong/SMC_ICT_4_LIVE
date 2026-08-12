"""Run the complete EasyChart decision sequence under the fixed simple contract.

Execution management remains one full entry, one full stop, one full target,
three-percent account risk, no daily governor, no time exit, and no trade-count
limit.  Research changes only context, causal state, entry location, invalidation
and target selection.
"""
from __future__ import annotations

import argparse
from datetime import date, timedelta
import json
from pathlib import Path

import pandas as pd

from backtest_support import make_engine, write_json
from fee_profiles_v5 import FEE_PROFILES, make_instrument_with_fee_profile
from instruments import CONTRACTS
from mtf_backtest_support_v5 import preserve_mtf_results_v5
from mtf_data import add_symbol_mtf_data
import mtf_strategy as _base_strategy
from mtf_strategy_v5 import EasyChartMTFConfig, EasyChartMTFStrategy
from scenario_acceptance_footprint_v18 import (
    ACCEPTANCE_FOOTPRINT_RULE,
    CompleteEasyChartBundleV18,
)
from scenario_close_detached_v14 import CLOSE_DETACHED_RETEST_RULE
from scenario_higher_timeframe_v15 import (
    FOUR_HOUR_ROLE_RULE,
    HIGHER_TIMEFRAME_ACCEPTANCE_RULE,
    HIGHER_TIMEFRAME_REVERSAL_RULE,
    HIGHER_TIMEFRAME_STATE_TRANSLATION,
)
from simple_contract_v14 import (
    FIXED_RISK_FRACTION,
    MINIMUM_GROSS_RR,
    contract_record,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", type=date.fromisoformat, required=True)
    parser.add_argument("--end", type=date.fromisoformat, required=True)
    parser.add_argument("--warmup-days", type=int, default=30)
    parser.add_argument("--symbols", nargs="+", default=list(CONTRACTS))
    parser.add_argument("--cache", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--fee-profile", choices=tuple(FEE_PROFILES), required=True)
    parser.add_argument("--entry-slippage-ticks", type=int, default=2)
    parser.add_argument("--stop-slippage-ticks", type=int, default=2)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.end < args.start:
        raise SystemExit("--end must be >= --start")
    if args.warmup_days < 1:
        raise SystemExit("--warmup-days must be positive")
    if args.entry_slippage_ticks < 0 or args.stop_slippage_ticks < 0:
        raise SystemExit("slippage ticks cannot be negative")

    symbols = tuple(args.symbols)
    unknown = sorted(set(symbols) - set(CONTRACTS))
    if unknown:
        raise SystemExit(f"unknown symbols: {unknown}")

    args.output.mkdir(parents=True, exist_ok=True)
    args.cache.mkdir(parents=True, exist_ok=True)
    profile = FEE_PROFILES[args.fee_profile]

    _base_strategy.MultiScaleScenarioBundle = CompleteEasyChartBundleV18

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

    strategy = EasyChartMTFStrategy(
        EasyChartMTFConfig(
            instrument_ids=tuple(item.id for item in instruments),
            higher_bar_types=tuple(higher_types),
            decision_bar_types=tuple(decision_types),
            trigger_bar_types=tuple(trigger_types),
            execution_bar_types=tuple(source_types),
            risk_fraction=float(FIXED_RISK_FRACTION),
            min_gross_rr=float(MINIMUM_GROSS_RR),
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
        metrics = preserve_mtf_results_v5(
            engine,
            strategy,
            args.output,
            symbols=symbols,
            start=args.start,
            end=args.end,
        )
        policy = {
            "candidate": "candidate-easychart-v18-complete-decision-sequence",
            "scale_policy": "4h_60m_context__15m_5m_1m_execution",
            "structure_policy": "MEANINGFUL_OBSERVABLE_STRUCTURES_ONLY_FOR_TRADE_CONTEXT",
            "target_policy": "nearest_confirmed_preexisting_opposite_objective_across_stack",
            "retest_policy": "CLOSE_DETACH_THEN_FIRST_RETURN",
            "retest_policy_provenance": CLOSE_DETACHED_RETEST_RULE,
            "acceptance_execution_policy": "BREAK_HOLD_RETEST_THEN_EVENT_LOCAL_FOOTPRINT",
            "acceptance_execution_provenance": ACCEPTANCE_FOOTPRINT_RULE,
            "higher_timeframe_acceptance_rule": HIGHER_TIMEFRAME_ACCEPTANCE_RULE,
            "higher_timeframe_reversal_rule": HIGHER_TIMEFRAME_REVERSAL_RULE,
            "four_hour_role_rule": FOUR_HOUR_ROLE_RULE,
            "higher_timeframe_state_translation": HIGHER_TIMEFRAME_STATE_TRANSLATION,
            "fee_profile": profile.name,
            "maker_fee_rate": float(profile.maker_rate),
            "taker_fee_rate": float(profile.taker_rate),
            "fee_profile_provenance": profile.provenance,
            "strategy_exit_policy": "PREDECLARED_NATIVE_FULL_STOP_OR_FULL_TARGET",
            **contract_record(),
        }
        metrics.update(policy)
        write_json(args.output / "metrics.json", metrics)

        run_path = args.output / "run.json"
        run_record = json.loads(run_path.read_text(encoding="utf-8"))
        run_record.update(policy)
        write_json(run_path, run_record)
        print(json.dumps(metrics, ensure_ascii=False, indent=2, sort_keys=True))
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
