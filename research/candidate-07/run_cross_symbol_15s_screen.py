#!/usr/bin/env python3
"""Untuned portability screen of the frozen 15S retest logic on project symbols."""
from __future__ import annotations

import argparse
import json
import multiprocessing as mp
from pathlib import Path
from typing import Any

import backtest as base
import backtest_pre_attack_value as replay
from binance_usdm_instruments import binance_usdm_perpetual
from nested_liquidity_sweep_scenario import (
    build_causal_signals as build_fifteen_second_signals,
    discover as discover_fifteen_second,
)
import run_local_liquidity_sweep_mss_retest as local
from smc_ict_4.manifest import write_json_atomic


PROJECT_SYMBOLS = ("BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT")


def _worker(
    args: argparse.Namespace,
    config_path: Path,
    symbol: str,
) -> None:
    original_discover = local.discover_structural_signals
    original_builder = local.build_causal_signals
    original_provider = replay.TestInstrumentProvider.btcusdt_perp_binance
    local.discover_structural_signals = (
        lambda *, config, bundle, start, end, require_retest: discover_fifteen_second(
            config=config,
            bundle=bundle,
            start=start,
            end=end,
            require_retest=require_retest,
            include_higher_sources=False,
        )
    )
    local.build_causal_signals = build_fifteen_second_signals
    replay.TestInstrumentProvider.btcusdt_perp_binance = staticmethod(
        lambda: binance_usdm_perpetual(symbol)
    )
    try:
        metrics = local._run_variant(
            args=args,
            config_path=config_path,
            variant="frozen_15s_retest",
            require_retest=True,
        )
        metrics["execution_contract"].update(
            {
                "selected_route": "15S liquidity sweep -> 15S MSS -> broken-level retest",
                "screen_symbol": symbol,
                "logic_or_parameter_change_from_btc": False,
                "single_pending_or_open_slot": True,
                "instrument_definition": "project grid, no arbitrary max notional",
            }
        )
        write_json_atomic(
            args.output.resolve() / "frozen_15s_retest" / "metrics.json",
            base._json_safe(metrics),
        )
    finally:
        local.discover_structural_signals = original_discover
        local.build_causal_signals = original_builder
        replay.TestInstrumentProvider.btcusdt_perp_binance = staticmethod(
            original_provider
        )
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
        raise RuntimeError(f"cross-symbol screen failed: {symbol} exit={process.exitcode}")
    path = symbol_args.output / "frozen_15s_retest" / "metrics.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise RuntimeError(f"invalid screen metrics: {path}")
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
        "family": "frozen_15s_sweep_mss_retest_portability_screen",
        "stage": "week-1",
        "period": {
            "start": args.start.isoformat(),
            "end_exclusive": args.end.isoformat(),
        },
        "symbols": list(requested),
        "source_commit_expected": args.source_commit,
        "engine": "NautilusTrader BacktestEngine",
        "risk_fraction": base_config["risk_fraction"],
        "maximum_hold_minutes": base_config["max_hold_minutes"],
        "logic_or_parameter_change_from_btc": False,
        "purpose": (
            "test structural portability before constructing the one-slot "
            "multi-instrument portfolio"
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
        default=["ETHUSDT"],
    )
    return parser


def main() -> int:
    return run(build_parser().parse_args())


if __name__ == "__main__":
    raise SystemExit(main())
