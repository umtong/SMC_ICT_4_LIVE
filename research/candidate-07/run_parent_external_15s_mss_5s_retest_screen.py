#!/usr/bin/env python3
"""Research screen for parent sweep -> 15S MSS -> same-boundary 5S retest.

The development and failed validation intervals are now research periods. This
runner changes only which clock owns the state transition: the parent source and
sweep remain on completed 15-second bars, a protected 15-second swing must break,
and five-second bars may only time the first rejection retest of that exact
boundary. Execution remains NautilusTrader with the unchanged cost/risk contract.
"""
from __future__ import annotations

import argparse
import json
import multiprocessing as mp
from pathlib import Path
from typing import Any

import backtest as base
import backtest_pre_attack_value as replay
from binance_usdm_instruments import binance_usdm_perpetual
from data_aggtrades_seeded import load_aggtrade_1s_bundle_seeded
from parent_external_15s_mss_5s_retest_scenario import (
    build_signals,
    discover,
)
import run_local_liquidity_sweep_mss_retest as local
from smc_ict_4.manifest import write_json_atomic


PROJECT_SYMBOLS = ("BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT")
VARIANT = "parent_external_15s_mss_5s_same_boundary_retest"


def _worker(
    args: argparse.Namespace,
    config_path: Path,
    symbol: str,
) -> None:
    original_discover = local.discover_structural_signals
    original_builder = local.build_causal_signals
    original_provider = replay.TestInstrumentProvider.btcusdt_perp_binance
    original_loader = replay.load_aggtrade_1s_bundle
    local.discover_structural_signals = discover
    local.build_causal_signals = build_signals
    replay.TestInstrumentProvider.btcusdt_perp_binance = staticmethod(
        lambda: binance_usdm_perpetual(symbol)
    )
    replay.load_aggtrade_1s_bundle = load_aggtrade_1s_bundle_seeded
    try:
        metrics = local._run_variant(
            args=args,
            config_path=config_path,
            variant=VARIANT,
            require_retest=True,
        )
        metrics["execution_contract"].update(
            {
                "selected_route": (
                    "1M/5M parent external first touch and failed attack -> "
                    "independent protected 15S displacement MSS -> first valid "
                    "5S rejection retest of the same broken 15S boundary"
                ),
                "screen_symbol": symbol,
                "source_timeframes": ["1M", "5M"],
                "mss_timeframe": "15S",
                "retest_timeframe": "5S",
                "five_second_clock_selects_direction_or_boundary": False,
                "same_boundary_retest_required": True,
                "physical_retest_window_seconds": 90,
                "changed_variable_from_parent_multiclock": (
                    "state confirmation clock: 5S or 15S first-MSS ensemble "
                    "versus mandatory 15S MSS with 5S entry timing only"
                ),
                "sweep_target_stop_risk_execution_logic_changed": False,
                "source_pool_reuse": False,
                "single_pending_or_open_slot": True,
                "instrument_definition": (
                    "verified project grid, no arbitrary maximum notional"
                ),
                "leading_zero_flow_policy": (
                    "carry last actual pre-window aggregate-trade price with "
                    "zero flow"
                ),
            }
        )
        write_json_atomic(
            args.output.resolve() / VARIANT / "metrics.json",
            base._json_safe(metrics),
        )
    finally:
        local.discover_structural_signals = original_discover
        local.build_causal_signals = original_builder
        replay.TestInstrumentProvider.btcusdt_perp_binance = staticmethod(
            original_provider
        )
        replay.load_aggtrade_1s_bundle = original_loader
        engine = getattr(local, "_EmptySignalSafeBacktestEngine", None)
        if engine is not None:
            engine.delegate_type = None


def _isolated(
    *,
    args: argparse.Namespace,
    config_path: Path,
    symbol: str,
) -> dict[str, Any]:
    symbol_args = argparse.Namespace(**vars(args))
    symbol_args.output = args.output.resolve() / symbol
    process = mp.get_context("spawn").Process(
        target=_worker,
        args=(symbol_args, config_path, symbol),
    )
    process.start()
    process.join()
    if process.exitcode != 0:
        raise RuntimeError(
            f"15S-state/5S-entry screen failed: {symbol} exit={process.exitcode}"
        )
    path = symbol_args.output / VARIANT / "metrics.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise RuntimeError(f"invalid hybrid screen metrics: {path}")
    return payload


def run(args: argparse.Namespace) -> int:
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    requested = tuple(symbol.upper() for symbol in args.symbols)
    if not requested or any(symbol not in PROJECT_SYMBOLS for symbol in requested):
        raise ValueError(f"symbols must be a subset of {PROJECT_SYMBOLS}")

    base_config = json.loads(args.config.read_text(encoding="utf-8"))
    base_config["max_hold_minutes"] = 30
    results: dict[str, Any] = {}
    for symbol in requested:
        symbol_root = output / symbol
        symbol_root.mkdir(parents=True, exist_ok=True)
        config = dict(base_config)
        config["symbol"] = symbol
        config_path = symbol_root / "frozen_config.json"
        write_json_atomic(config_path, config)
        metrics = _isolated(
            args=args,
            config_path=config_path,
            symbol=symbol,
        )
        results[symbol] = local._compact(metrics)

    summary = {
        "candidate": "candidate-07",
        "family": "parent_external_15S_MSS_5S_same_boundary_retest_screen",
        "stage": args.screen_stage,
        "period": {
            "start": args.start.isoformat(),
            "end_exclusive": args.end.isoformat(),
        },
        "symbols": list(requested),
        "source_commit_expected": args.source_commit,
        "engine": "NautilusTrader BacktestEngine",
        "risk_fraction": base_config["risk_fraction"],
        "maximum_hold_minutes": base_config["max_hold_minutes"],
        "changed_variable": (
            "mandatory 15S state confirmation before 5S entry timing"
        ),
        "unchanged_components": [
            "causal unconsumed 1M/5M source liquidity",
            "15S first-touch attack-flow sweep and reclaim",
            "protected-swing displacement definition",
            "same broken-boundary rejection retest semantics",
            "15S/1M/5M target hierarchy",
            "source-extreme structure stop",
            "fees adverse ticks funding reserve",
            "current-full-NAV 3% planned-loss sizing",
            "single pending/open slot",
        ],
        "purpose": (
            "test whether 15S state confirmation removes parent-external XRP "
            "false reversals while 5S retest timing preserves opportunity"
        ),
        "results": results,
    }
    write_json_atomic(output / "summary.json", summary)
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = local.build_parser()
    parser.add_argument(
        "--symbols",
        nargs="+",
        default=["BTCUSDT", "XRPUSDT"],
    )
    parser.add_argument("--screen-stage", default="research-period")
    return parser


def main() -> int:
    return run(build_parser().parse_args())


if __name__ == "__main__":
    raise SystemExit(main())
