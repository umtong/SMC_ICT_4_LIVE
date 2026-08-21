"""Run EasyChart v2 through one NautilusTrader continuous account."""
from __future__ import annotations

import argparse
from datetime import date, timedelta
import json
from pathlib import Path

import pandas as pd

from backtest_support import add_symbol_data, make_engine, preserve_results
from instruments import CONTRACTS, make_instrument
from strategy import EasyChartV2Config, EasyChartV2Strategy


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", type=date.fromisoformat, required=True)
    parser.add_argument("--end", type=date.fromisoformat, required=True)
    parser.add_argument("--warmup-days", type=int, default=7)
    parser.add_argument("--symbols", nargs="+", default=list(CONTRACTS))
    parser.add_argument("--cache", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--min-prominence-atr", type=float, default=1.0)
    parser.add_argument("--rejection-only", action="store_true")
    parser.add_argument("--acceptance-only", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.end < args.start:
        raise SystemExit("--end must be >= --start")
    if args.rejection_only and args.acceptance_only:
        raise SystemExit("choose at most one of --rejection-only/--acceptance-only")
    symbols = tuple(args.symbols)
    unknown = sorted(set(symbols) - set(CONTRACTS))
    if unknown:
        raise SystemExit(f"unknown symbols: {unknown}")
    args.output.mkdir(parents=True, exist_ok=True)
    args.cache.mkdir(parents=True, exist_ok=True)

    engine = make_engine()
    instruments = [make_instrument(symbol) for symbol in symbols]
    source_types = []
    signal_types = []
    load_start = args.start - timedelta(days=args.warmup_days)
    for symbol, instrument in zip(symbols, instruments, strict=True):
        engine.add_instrument(instrument)
        source_type, signal_type = add_symbol_data(
            engine, symbol, instrument, load_start, args.end, args.cache,
        )
        source_types.append(source_type)
        signal_types.append(signal_type)
    engine.sort_data()

    enable_rejection = not args.acceptance_only
    enable_acceptance = not args.rejection_only
    strategy = EasyChartV2Strategy(
        EasyChartV2Config(
            instrument_ids=tuple(item.id for item in instruments),
            signal_bar_types=tuple(signal_types),
            execution_bar_types=tuple(source_types),
            min_prominence_atr=args.min_prominence_atr,
            enable_rejection=enable_rejection,
            enable_acceptance=enable_acceptance,
            trading_start_ns=int(pd.Timestamp(args.start, tz="UTC").value),
        ),
    )
    engine.add_strategy(strategy)
    try:
        engine.run()
        metrics = preserve_results(
            engine,
            strategy,
            args.output,
            symbols=symbols,
            instruments=instruments,
            start=args.start,
            end=args.end,
            min_prominence_atr=args.min_prominence_atr,
            enable_rejection=enable_rejection,
            enable_acceptance=enable_acceptance,
        )
        print(json.dumps(metrics, ensure_ascii=False, indent=2, sort_keys=True))
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
