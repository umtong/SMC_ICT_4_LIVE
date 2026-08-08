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
from smc_ict_4.manifest import write_json_atomic

_ORIGINAL_IMPORTABLE_STRATEGY_CONFIG = candidate05_backtest.ImportableStrategyConfig
_ORIGINAL_BACKTEST_VENUE_CONFIG = candidate05_backtest.BacktestVenueConfig


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


def _candidate18_venue_config(*args: Any, **kwargs: Any):
    """Make partial OTO child release explicit in the shared venue."""
    requested = kwargs.get("oto_trigger_mode")
    if requested not in (None, "PARTIAL"):
        raise RuntimeError(f"Candidate 18 requires PARTIAL OTO, got {requested!r}")
    kwargs["oto_trigger_mode"] = "PARTIAL"
    return _ORIGINAL_BACKTEST_VENUE_CONFIG(*args, **kwargs)


candidate05_backtest.ImportableStrategyConfig = _candidate18_strategy_config
candidate05_backtest.BacktestVenueConfig = _candidate18_venue_config


def run_stage(args: argparse.Namespace) -> dict[str, Any]:
    result = candidate05_backtest.run_backtest(
        config_path=args.config,
        build_start=date.fromisoformat(args.build_start),
        build_end=date.fromisoformat(args.build_end),
        evaluation_start=date.fromisoformat(args.evaluation_start),
        evaluation_end=date.fromisoformat(args.evaluation_end),
        cache=args.cache,
        output=args.output,
    )
    result["candidate"] = "candidate-18-v3-partial-oto-ioc-router"
    result["validation_mode"] = args.validation_mode
    result["reused_runner"] = "research/candidate-05/backtest.py"
    result["reused_execution"] = "research/candidate-16/strategy_v2.py"
    result["reused_state"] = "research/candidate-17/remembered_defense_strategy.py"
    result["strategy_path"] = "research/candidate-18/candidate18_strategy.py"
    result["strategy_implementation"] = (
        "research/candidate-18/partial_oto_ioc_strategy.py"
    )
    result["oto_trigger_mode"] = "PARTIAL"
    write_json_atomic(args.output.resolve() / "metrics.json", result)
    write_json_atomic(
        args.output.resolve() / "candidate18_contract.json",
        {
            "candidate": result["candidate"],
            "engine": "NautilusTrader BacktestNode",
            "risk_fraction": 0.03,
            "max_global_entry_or_position": 1,
            "decision_policy": {
                "failed_auction_reversal": [
                    "CLEAN_FIRST_DISPLAYED_DEFENSE",
                    "BOUNDARY_RECLAIM",
                    "STRICTLY_LATER_OPPOSITE_INITIATIVE",
                    "FULL_WINDOW_PERSISTENCE_OR_FIRST_BAR_NOTIONAL_SHOCK",
                    "COMPLETED_SIGNAL_EXECUTION",
                    "PRICE_CAPPED_IOC_LIMIT_PARENT",
                ],
                "true_acceptance": [
                    "OUTSIDE_RESIDENCE",
                    "DIRECTIONAL_BOOK_WITHDRAWAL",
                    "FRESH_OPEN_INTEREST_EXPANSION",
                    "FIRST_DEFENDED_RETEST",
                    "COMPLETED_SIGNAL_EXECUTION",
                    "PRICE_CAPPED_IOC_LIMIT_PARENT",
                ],
                "remembered_defense": "UNRESOLVED_NO_TRADE_WITHOUT_DEPLETION_PROOF",
                "otherwise": "UNRESOLVED_NO_TRADE",
            },
            "entry_policy": {
                "type": "IOC_LIMIT_BRACKET",
                "worst_fill_cap": "50% expansion of structural stop distance",
                "fill_policy": "immediate full or partial fill at or better than cap",
                "oto_trigger_mode": "PARTIAL",
                "partial_fill_protection": (
                    "release and resize stop/target children pro-rata for every fill"
                ),
                "risk_sizing": "worst permissible limit fill including configured costs",
            },
            "target_policy": "unconsumed liquidity objective after costs",
            "v1_failure_replaced": (
                "explicit PARTIAL OTO prevents naked IOC partial exposure"
            ),
            "v2_fok_tradeoff": (
                "FOK was safe but discarded partial-fill opportunities and failed both viewed weeks"
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
