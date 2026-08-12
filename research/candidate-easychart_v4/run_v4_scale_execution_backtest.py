"""Run source-faithful accepted-break policy at MACRO or dual execution scale."""
from __future__ import annotations

import argparse
from datetime import date, timedelta
import json
from pathlib import Path

import pandas as pd

from backtest_support import make_engine
from instruments import CONTRACTS, make_instrument
from mtf_backtest_support import preserve_mtf_results
from mtf_data import add_symbol_mtf_data
import mtf_strategy as _base_strategy
from mtf_strategy_v4_scale_execution import (
    DualScaleFirstAvailableEasyChartStrategy,
    EasyChartMTFConfig,
    MacroOnlyEasyChartStrategy,
)
from scenario_runtime_v4_acceptance_gate import (
    SourceFaithfulRetestEntryGatedBundle,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", type=date.fromisoformat, required=True)
    parser.add_argument("--end", type=date.fromisoformat, required=True)
    parser.add_argument("--warmup-days", type=int, default=30)
    parser.add_argument("--symbols", nargs="+", default=list(CONTRACTS))
    parser.add_argument("--cache", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--execution-policy",
        choices=("macro-only", "dual-first-available"),
        required=True,
    )
    parser.add_argument("--min-gross-rr", type=float, default=1.0)
    parser.add_argument("--entry-slippage-ticks", type=int, default=2)
    parser.add_argument("--stop-slippage-ticks", type=int, default=2)
    return parser.parse_args()


def _rewrite_identity(
    output: Path,
    metrics: dict[str, object],
    *,
    execution_policy: str,
) -> None:
    candidate = f"candidate-easychart_v4-{execution_policy}"
    metrics["candidate"] = candidate
    metrics["execution_policy"] = execution_policy
    metrics["context_policy"] = "retest"
    metrics["trap_semantics"] = (
        "immediate-reentry=fakeout; "
        "accepted-break+later-confirmed-outside-pivot+first-reentry=trap"
    )
    metrics["acceptance_entry_semantics"] = (
        "accepted break -> first structural S/R-flip retest holds -> "
        "event-local displacement -> first later footprint retest"
    )
    metrics["scale_semantics"] = (
        "macro-only=1h->5m; dual-first-available=first causal 1h->5m or "
        "15m->1m plan, at most one submitted trade per confirmed 1h event"
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
                "execution_policy": execution_policy,
                "context_policy": "retest",
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

    strategy_type = (
        MacroOnlyEasyChartStrategy
        if args.execution_policy == "macro-only"
        else DualScaleFirstAvailableEasyChartStrategy
    )
    _base_strategy.MultiScaleScenarioBundle = SourceFaithfulRetestEntryGatedBundle

    args.output.mkdir(parents=True, exist_ok=True)
    args.cache.mkdir(parents=True, exist_ok=True)
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

    strategy = strategy_type(
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
        metrics = preserve_mtf_results(
            engine,
            strategy,
            args.output,
            symbols=symbols,
            start=args.start,
            end=args.end,
        )
        _rewrite_identity(
            args.output,
            metrics,
            execution_policy=args.execution_policy,
        )
        print(json.dumps(metrics, ensure_ascii=False, indent=2, sort_keys=True))
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
