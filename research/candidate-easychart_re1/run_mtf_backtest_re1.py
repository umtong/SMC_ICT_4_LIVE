"""Run EasyChart RE1 in one four-symbol continuous NautilusTrader account."""
from __future__ import annotations

import argparse
from datetime import date, timedelta
import json
from pathlib import Path

import pandas as pd

from backtest_support import make_engine, write_json
from easychart_re1 import (
    HTF_DIRECTION_RULE,
    HTF_NEUTRAL_RULE,
    HTF_REVERSAL_AREA_RULE,
)
from easychart_re1_fresh import FRESH_HTF_FOOTPRINT_RULE
from easychart_re1_horizontal import (
    HORIZONTAL_FLIP_STOP_RULE,
    REPEATED_DEFENSE_LIFECYCLE_RULE,
    REPEATED_DEFENSE_RULE,
)
from easychart_re1_natural import (
    EasyChartRE1NaturalBundle,
    HORIZONTAL_SWEEP_RECLAIM_ONLY_RULE,
)
from execution_re1 import EasyChartMTFConfig, EasyChartRE1Strategy, LIVE_PROTECTION_POLICY
from fee_profiles_v5 import FEE_PROFILES, make_instrument_with_fee_profile
from instruments import CONTRACTS
from mtf_backtest_support_v5 import preserve_mtf_results_v5
from mtf_data import add_symbol_mtf_data
import mtf_strategy as _base_strategy
from scenario_channel_extension_v16 import CHANNEL_EXTENSION_RULE
from scenario_close_detached_v14 import CLOSE_DETACHED_RETEST_RULE
from simple_contract_v14 import FIXED_RISK_FRACTION, MINIMUM_GROSS_RR, contract_record


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
    _base_strategy.MultiScaleScenarioBundle = EasyChartRE1NaturalBundle

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

    strategy = EasyChartRE1Strategy(
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
        metadata = {
            "candidate": "candidate-easychart_re1",
            "decision_policy": (
                "60m causal direction -> independent 15m diagonal or repeated-defense sweep location -> "
                "5m event -> 1m first distinct retest -> immutable pre-entry stop and target -> "
                "one full-position exit"
            ),
            "scale_policy": "60m_context_router_plus_natural_15_5_1_scenario_families",
            "context_router_policy": (
                "continuation with 60m BOS; neutral regime allowed; countertrend only at "
                "same-side untouched or first-touch-episode 60m structure/OB/FVG"
            ),
            "context_router_provenance": [
                HTF_DIRECTION_RULE,
                HTF_REVERSAL_AREA_RULE,
                HTF_NEUTRAL_RULE,
                FRESH_HTF_FOOTPRINT_RULE,
            ],
            "htf_footprint_lifecycle": "UNTOUCHED_OR_FIRST_COMPLETED_60M_TOUCH_EPISODE",
            "trade_context_policy": (
                "CHANNEL_TRENDLINE_CORE_PLUS_REPEATED_DEFENSE_SWEEP_RECLAIM_FAMILY"
            ),
            "horizontal_policy_provenance": [
                REPEATED_DEFENSE_RULE,
                REPEATED_DEFENSE_LIFECYCLE_RULE,
                HORIZONTAL_FLIP_STOP_RULE,
                HORIZONTAL_SWEEP_RECLAIM_ONLY_RULE,
            ],
            "target_policy": "CHANNEL_EDGE_OR_PREEXISTING_OPPOSING_STRUCTURE_WITH_CHANNEL_EXTENSION",
            "target_policy_provenance": CHANNEL_EXTENSION_RULE,
            "retest_policy": "CLOSE_DETACH_THEN_FIRST_RETURN",
            "retest_policy_provenance": CLOSE_DETACHED_RETEST_RULE,
            "position_management": (
                "ONE_FULL_POSITION_WITH_IMMUTABLE_PRE_ENTRY_STOP_AND_TARGET_NO_PARTIAL_NO_RATCHET"
            ),
            "position_management_provenance": "RE1_SIMPLE_FIXED_PLAN_CONTRACT",
            "execution_policy": LIVE_PROTECTION_POLICY,
            "fee_profile": profile.name,
            "maker_fee_rate": float(profile.maker_rate),
            "taker_fee_rate": float(profile.taker_rate),
            "fee_profile_provenance": profile.provenance,
            "strategy_exit_policy": (
                "REDUCE_ONLY_STOP_MARKET_OR_LIMIT_TARGET_WITH_STRATEGY_SIBLING_CANCEL"
            ),
            **contract_record(),
        }
        metrics.update(metadata)
        write_json(args.output / "metrics.json", metrics)

        run_path = args.output / "run.json"
        run_record = json.loads(run_path.read_text(encoding="utf-8"))
        run_record.update(metadata)
        write_json(run_path, run_record)
        print(json.dumps(metrics, ensure_ascii=False, indent=2, sort_keys=True))
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
