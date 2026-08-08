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


candidate05_backtest.ImportableStrategyConfig = _candidate18_strategy_config


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
    result["candidate"] = "candidate-18-v2-fok-execution-preserving-router"
    result["validation_mode"] = args.validation_mode
    result["reused_runner"] = "research/candidate-05/backtest.py"
    result["reused_execution"] = "research/candidate-16/strategy_v2.py"
    result["reused_state"] = "research/candidate-17/remembered_defense_strategy.py"
    result["strategy_path"] = "research/candidate-18/candidate18_strategy.py"
    result["strategy_implementation"] = "research/candidate-18/fok_capped_strategy.py"
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
                    "PRICE_CAPPED_FOK_LIMIT_PARENT",
                ],
                "true_acceptance": [
                    "OUTSIDE_RESIDENCE",
                    "DIRECTIONAL_BOOK_WITHDRAWAL",
                    "FRESH_OPEN_INTEREST_EXPANSION",
                    "FIRST_DEFENDED_RETEST",
                    "COMPLETED_SIGNAL_EXECUTION",
                    "PRICE_CAPPED_FOK_LIMIT_PARENT",
                ],
                "remembered_defense": "UNRESOLVED_NO_TRADE_WITHOUT_DEPLETION_PROOF",
                "otherwise": "UNRESOLVED_NO_TRADE",
            },
            "entry_policy": {
                "type": "FOK_LIMIT_BRACKET",
                "signal": "completed causal initiative or defended acceptance retest",
                "worst_fill_cap": "50% expansion of structural stop distance",
                "fill_policy": "fill immediately in full at or better than cap, otherwise cancel all",
                "risk_sizing": "worst permissible limit fill including configured costs",
            },
            "target_policy": "unconsumed liquidity objective after costs",
            "v1_failure_replaced": (
                "IOC partial fill canceled OTO children and left naked exposure"
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
