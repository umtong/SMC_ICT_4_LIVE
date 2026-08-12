"""Run the 4h-event-routed EasyChart v4 MICRO execution system."""
from __future__ import annotations

import argparse
from datetime import date, timedelta
import json
from pathlib import Path

import pandas as pd

from backtest_support import make_engine
from instruments import CONTRACTS, make_instrument
from mtf_backtest_support import preserve_mtf_results
from mtf_data_4h import add_symbol_4h_mtf_data
import mtf_strategy as _base_strategy
from mtf_strategy_v4_4h import FourHourEasyChartConfig, FourHourEasyChartStrategy
from scenario_runtime_v4_4h import FourHourRoutedResearchBundle


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", type=date.fromisoformat, required=True)
    parser.add_argument("--end", type=date.fromisoformat, required=True)
    parser.add_argument("--warmup-days", type=int, default=60)
    parser.add_argument("--symbols", nargs="+", default=list(CONTRACTS))
    parser.add_argument("--cache", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--min-gross-rr", type=float, default=1.0)
    parser.add_argument("--entry-slippage-ticks", type=int, default=2)
    parser.add_argument("--stop-slippage-ticks", type=int, default=2)
    return parser.parse_args()


def _rewrite_identity(output: Path, metrics: dict[str, object]) -> None:
    candidate = "candidate-easychart_v4-live-4h-event-router"
    metrics["candidate"] = candidate
    metrics["context_policy"] = "latest live 4h event -> live 1h event -> 15m->1m"
    metrics["super_context_semantics"] = (
        "a lower plan is executable only while a fully confirmed still-live 4h "
        "structural event has the same side; 4h acceptance waits for first 1h retest"
    )
    (output / "metrics.json").write_text(
        json.dumps(metrics, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    run_path = output / "run.json"
    if run_path.exists():
        run = json.loads(run_path.read_text(encoding="utf-8"))
        run.update(
            {
                "candidate": candidate,
                "context_policy": "LIVE_4H_THEN_LIVE_1H_EVENT",
            },
        )
        run_path.write_text(
            json.dumps(run, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )


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

    _base_strategy.MultiScaleScenarioBundle = FourHourRoutedResearchBundle
    args.output.mkdir(parents=True, exist_ok=True)
    args.cache.mkdir(parents=True, exist_ok=True)
    engine = make_engine()
    instruments = [make_instrument(symbol) for symbol in symbols]
    source_types = []
    trigger_types = []
    decision_types = []
    higher_types = []
    super_types = []
    load_start = args.start - timedelta(days=args.warmup_days)
    for symbol, instrument in zip(symbols, instruments, strict=True):
        engine.add_instrument(instrument)
        source, trigger, decision, higher, super_type = add_symbol_4h_mtf_data(
            engine,
            symbol,
            instrument,
            load_start,
            args.end,
            args.cache,
        )
        source_types.append(source)
        trigger_types.append(trigger)
        decision_types.append(decision)
        higher_types.append(higher)
        super_types.append(super_type)
    engine.sort_data()

    strategy = FourHourEasyChartStrategy(
        FourHourEasyChartConfig(
            instrument_ids=tuple(item.id for item in instruments),
            super_bar_types=tuple(super_types),
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
        metrics = preserve_mtf_results(
            engine,
            strategy,
            args.output,
            symbols=symbols,
            start=args.start,
            end=args.end,
        )
        _rewrite_identity(args.output, metrics)
        print(json.dumps(metrics, ensure_ascii=False, indent=2, sort_keys=True))
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
