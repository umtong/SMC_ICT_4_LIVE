#!/usr/bin/env python3
"""Candidate 18 entry point; reuses Candidate 05's NautilusTrader runner."""
from __future__ import annotations

import argparse
from datetime import date
import json
from pathlib import Path
import sys
from typing import Any

HERE = Path(__file__).resolve().parent
CANDIDATE17 = HERE.parent / "candidate-17"
CANDIDATE16 = HERE.parent / "candidate-16"
CANDIDATE05 = HERE.parent / "candidate-05"
sys.path.insert(0, str(CANDIDATE16))
sys.path.insert(1, str(HERE))
sys.path.insert(2, str(CANDIDATE17))
sys.path.insert(3, str(CANDIDATE05))

from timestamp_contract import install as install_timestamp_contract
from wrangler_contract import install as install_wrangler_contract
from positioning_contract import install as install_positioning_contract
from basis_contract import install as install_basis_contract
from book_depth_gap_contract import install as install_book_depth_gap_contract

install_timestamp_contract()
install_wrangler_contract()
install_positioning_contract()
install_basis_contract()
install_book_depth_gap_contract()

import backtest as candidate05_backtest
from nautilus_trader.model.data import TradeTick
from smc_ict_4.manifest import write_json_atomic
from trade_tick_catalog import add_trade_ticks_to_catalog

_ORIGINAL_IMPORTABLE_STRATEGY_CONFIG = (
    candidate05_backtest.ImportableStrategyConfig
)
_ORIGINAL_PREPARE_CATALOG = candidate05_backtest.prepare_catalog
_ORIGINAL_BACKTEST_DATA_CONFIG = candidate05_backtest.BacktestDataConfig
_ORIGINAL_BACKTEST_RUN_CONFIG = candidate05_backtest.BacktestRunConfig
_ORIGINAL_BACKTEST_VENUE_CONFIG = candidate05_backtest.BacktestVenueConfig
_TRADE_EXECUTION_START: date | None = None
_TRADE_EXECUTION_END: date | None = None


def _candidate18_strategy_config(
    *,
    strategy_path: str,
    config_path: str,
    config: dict[str, Any],
):
    del strategy_path, config_path
    return _ORIGINAL_IMPORTABLE_STRATEGY_CONFIG(
        strategy_path="candidate18_strategy:Candidate18Strategy",
        config_path="candidate18_strategy:Candidate18Config",
        config=config,
    )


def _candidate18_prepare_catalog(**kwargs: Any):
    if _TRADE_EXECUTION_START is None or _TRADE_EXECUTION_END is None:
        raise RuntimeError("candidate18 execution range was not initialized")
    instrument, manifest_path = _ORIGINAL_PREPARE_CATALOG(**kwargs)
    trade_manifest = add_trade_ticks_to_catalog(
        instrument=instrument,
        catalog_path=Path(kwargs["catalog_path"]),
        raw_files=list(kwargs["raw_files"]),
        output=Path(kwargs["output"]),
        build_start=kwargs["build_start"],
        build_end=kwargs["build_end"],
        execution_start=_TRADE_EXECUTION_START,
        execution_end=_TRADE_EXECUTION_END,
    )
    if int(trade_manifest["trade_ticks"]) <= 0:
        raise RuntimeError(
            "candidate18 requires native TradeTick execution data",
        )
    return instrument, manifest_path


def _candidate18_venue_config(*args: Any, **kwargs: Any):
    # Bars remain the strategy clock. Only raw aggTrades advance matching.
    kwargs["bar_execution"] = False
    kwargs["trade_execution"] = True
    return _ORIGINAL_BACKTEST_VENUE_CONFIG(*args, **kwargs)


def _candidate18_run_config(*args: Any, **kwargs: Any):
    if args:
        raise TypeError(
            "candidate18 expects keyword BacktestRunConfig construction",
        )
    data = list(kwargs.get("data") or [])
    if not data:
        raise RuntimeError("candidate18 received no BacktestDataConfig")
    bar_data = data[0]
    trade_data = _ORIGINAL_BACKTEST_DATA_CONFIG(
        catalog_path=bar_data.catalog_path,
        data_cls=TradeTick,
        instrument_id=bar_data.instrument_id,
        start_time=bar_data.start_time,
        end_time=bar_data.end_time,
    )
    kwargs["data"] = [trade_data, *data]
    return _ORIGINAL_BACKTEST_RUN_CONFIG(**kwargs)


candidate05_backtest.ImportableStrategyConfig = _candidate18_strategy_config
candidate05_backtest.prepare_catalog = _candidate18_prepare_catalog
candidate05_backtest.BacktestVenueConfig = _candidate18_venue_config
candidate05_backtest.BacktestRunConfig = _candidate18_run_config


def run_stage(args: argparse.Namespace) -> dict[str, Any]:
    global _TRADE_EXECUTION_START, _TRADE_EXECUTION_END
    _TRADE_EXECUTION_START = date.fromisoformat(args.evaluation_start)
    _TRADE_EXECUTION_END = date.fromisoformat(args.evaluation_end)
    result = candidate05_backtest.run_backtest(
        config_path=args.config,
        build_start=date.fromisoformat(args.build_start),
        build_end=date.fromisoformat(args.build_end),
        evaluation_start=date.fromisoformat(args.evaluation_start),
        evaluation_end=date.fromisoformat(args.evaluation_end),
        cache=args.cache,
        output=args.output,
    )
    trade_manifest = json.loads(
        (args.output.resolve() / "trade_tick_manifest.json").read_text(
            encoding="utf-8",
        ),
    )
    result.update(
        {
            "candidate": "candidate-18-v6-bounded-gtd-router",
            "validation_mode": args.validation_mode,
            "reused_runner": "research/candidate-05/backtest.py",
            "reused_execution": "research/candidate-16/strategy_v2.py",
            "reused_state": (
                "research/candidate-17/remembered_defense_strategy.py"
            ),
            "strategy_path": (
                "research/candidate-18/candidate18_strategy.py"
            ),
            "strategy_implementation": (
                "research/candidate-18/bounded_gtd_entry_strategy.py"
            ),
            "execution_data": (
                "Binance aggTrades -> NautilusTrader TradeTick"
            ),
            "trade_ticks": int(trade_manifest["trade_ticks"]),
            "bar_execution": False,
            "trade_execution": True,
            "entry_execution": (
                "one non-chasing price-capped GTD LIMIT with a bounded "
                "micro-auction fill window"
            ),
            "protective_trigger": (
                "local LAST_PRICE emulation; native MARKET release"
            ),
        },
    )
    write_json_atomic(args.output.resolve() / "metrics.json", result)
    write_json_atomic(
        args.output.resolve() / "candidate18_contract.json",
        {
            "candidate": result["candidate"],
            "engine": "NautilusTrader BacktestNode",
            "risk_fraction": 0.03,
            "max_global_entry_or_position": 1,
            "state_router": (
                "full-window persistence or first-bar notional shock; "
                "acceptance uses book withdrawal plus fresh OI; otherwise "
                "no-trade"
            ),
            "entry": (
                "one native GTD LIMIT at the original worst-fill cap, "
                "active for a strictly bounded micro-auction window"
            ),
            "protection": (
                "each actual fill receives independent reduce-only "
                "LAST_PRICE-emulated STOP_MARKET and LIMIT target"
            ),
            "stop_release": (
                "actual TradeTick stop crossing releases a native "
                "reduce-only MARKET through configured latency"
            ),
            "entry_chasing": False,
            "parent_cancellation_dependency": False,
            "risk_sizing": (
                "total requested quantity is bounded at three percent "
                "planned loss using the worst permissible fill price, "
                "fees and adverse slippage"
            ),
            "runner_snapshot": (
                "candidate-17@3efdf932d37bb997cff95404fb40ee7026a58325"
            ),
        },
    )
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    stage = sub.add_parser("stage")
    stage.add_argument("--config", type=Path, required=True)
    stage.add_argument("--build-start", required=True)
    stage.add_argument("--build-end", required=True)
    stage.add_argument("--evaluation-start", required=True)
    stage.add_argument("--evaluation-end", required=True)
    stage.add_argument("--cache", type=Path, required=True)
    stage.add_argument("--output", type=Path, required=True)
    stage.add_argument("--validation-mode", required=True)
    args = parser.parse_args()
    result = run_stage(args)
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
