"""Run a causal EasyChart v5 objective-policy ablation through NautilusTrader."""
from __future__ import annotations

import argparse
from datetime import date, timedelta
import json
from pathlib import Path

import pandas as pd

from backtest_support import make_engine, write_json
from instruments import CONTRACTS, make_instrument
from mtf_backtest_support_v5 import preserve_mtf_results_v5
from mtf_data import add_symbol_mtf_data
import mtf_strategy as _base_strategy
from mtf_strategy_v5 import EasyChartMTFConfig, EasyChartMTFStrategy
from scenario_target_ablation_v5 import BUNDLE_BY_TARGET_POLICY


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", type=date.fromisoformat, required=True)
    parser.add_argument("--end", type=date.fromisoformat, required=True)
    parser.add_argument("--warmup-days", type=int, default=30)
    parser.add_argument("--symbols", nargs="+", default=list(CONTRACTS))
    parser.add_argument("--cache", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--target-policy",
        choices=tuple(BUNDLE_BY_TARGET_POLICY),
        required=True,
    )
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
    if args.min_gross_rr <= 0.0:
        raise SystemExit("--min-gross-rr must be positive")
    if args.entry_slippage_ticks < 0 or args.stop_slippage_ticks < 0:
        raise SystemExit("slippage ticks cannot be negative")
    symbols = tuple(args.symbols)
    unknown = sorted(set(symbols) - set(CONTRACTS))
    if unknown:
        raise SystemExit(f"unknown symbols: {unknown}")
    args.output.mkdir(parents=True, exist_ok=True)
    args.cache.mkdir(parents=True, exist_ok=True)

    _base_strategy.MultiScaleScenarioBundle = BUNDLE_BY_TARGET_POLICY[args.target_policy]

    engine = make_engine()
    instruments = [make_instrument(symbol) for symbol in symbols]
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
            min_gross_rr=args.min_gross_rr,
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
        metrics["target_policy"] = args.target_policy
        write_json(args.output / "metrics.json", metrics)
        run_path = args.output / "run.json"
        run_record = json.loads(run_path.read_text(encoding="utf-8"))
        run_record["target_policy"] = args.target_policy
        run_record["diagnostic_only"] = True
        write_json(run_path, run_record)
        print(json.dumps(metrics, ensure_ascii=False, indent=2, sort_keys=True))
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
