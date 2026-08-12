"""Run the channel/trend-line core under the fixed EasyChart contract."""
from __future__ import annotations

import argparse
from datetime import date, timedelta
import json
from pathlib import Path

import pandas as pd

from backtest_support import make_engine, write_json
from diagonal_core_v20 import (
    DIAGONAL_CORE_RULE,
    MAINLINE_ORIGIN_STOP_RULE_V20,
    MicroDiagonalCoreBundleV20,
)
from fee_profiles_v5 import FEE_PROFILES, make_instrument_with_fee_profile
from instruments import CONTRACTS
from mtf_backtest_support_v5 import preserve_mtf_results_v5
from mtf_data import add_symbol_mtf_data
import mtf_strategy as _base_strategy
from mtf_strategy_v5 import EasyChartMTFConfig, EasyChartMTFStrategy
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
    _base_strategy.MultiScaleScenarioBundle = MicroDiagonalCoreBundleV20

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
        metadata = {
            "candidate": "candidate-easychart-v20-diagonal-core",
            "scale_policy": "micro_only",
            "trade_context_policy": "CHANNEL_AND_TRENDLINE_ONLY",
            "trade_context_provenance": DIAGONAL_CORE_RULE,
            "channel_mainline_reversal_stop_policy": "BREAKOUT_WAVE_ORIGIN",
            "channel_mainline_reversal_stop_provenance": MAINLINE_ORIGIN_STOP_RULE_V20,
            "target_policy": "CHANNEL_ACCEPTANCE_FIRST_OBJECTIVE_OTHERWISE_EXISTING",
            "target_policy_provenance": CHANNEL_EXTENSION_RULE,
            "retest_policy": "CLOSE_DETACH_THEN_FIRST_RETURN",
            "retest_policy_provenance": CLOSE_DETACHED_RETEST_RULE,
            "fee_profile": profile.name,
            "maker_fee_rate": float(profile.maker_rate),
            "taker_fee_rate": float(profile.taker_rate),
            "fee_profile_provenance": profile.provenance,
            "strategy_exit_policy": "PREDECLARED_NATIVE_FULL_STOP_OR_FULL_TARGET",
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
