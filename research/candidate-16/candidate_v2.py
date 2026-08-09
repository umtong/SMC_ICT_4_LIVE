#!/usr/bin/env python3
"""Candidate 16 v2 entry point; reuses Candidate 05's NautilusTrader runner."""
from __future__ import annotations

import argparse
from datetime import date
import json
from pathlib import Path
import sys
from typing import Any

HERE = Path(__file__).resolve().parent
CANDIDATE05 = HERE.parent / "candidate-05"
sys.path.insert(0, str(HERE))
sys.path.insert(1, str(CANDIDATE05))

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


def _candidate16_v2_strategy_config(
    *,
    strategy_path: str,
    config_path: str,
    config: dict[str, Any],
):
    del strategy_path, config_path
    return _ORIGINAL_IMPORTABLE_STRATEGY_CONFIG(
        strategy_path="strategy_v2:Candidate16V2Strategy",
        config_path="strategy_v2:Candidate16V2Config",
        config=config,
    )


candidate05_backtest.ImportableStrategyConfig = _candidate16_v2_strategy_config


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
    result["candidate"] = "candidate-16-v2-displayed-liquidity-later-initiative"
    result["validation_mode"] = "screen"
    result["reused_runner"] = "research/candidate-05/backtest.py"
    result["strategy_path"] = "research/candidate-16/strategy_v2.py"
    write_json_atomic(args.output.resolve() / "metrics.json", result)
    write_json_atomic(
        args.output.resolve() / "candidate16_v2_contract.json",
        {
            "candidate": result["candidate"],
            "engine": "NautilusTrader BacktestNode",
            "risk_fraction": 0.03,
            "max_global_entry_or_position": 1,
            "failure_sequence": [
                "PARENT_ATTACK",
                "DISPLAYED_LIQUIDITY_DEFENSE",
                "COMPLETED_RECLAIM",
                "FAILURE_FROZEN_NO_ORDER",
                "LATER_PRICE_FLOW_QUEUE_INITIATIVE",
                "ENTRY",
            ],
            "acceptance_sequence": [
                "OUTSIDE_RESIDENCE",
                "DIRECTIONAL_BOOK_SUPPORT",
                "LIQUIDITY_AHEAD_WITHDRAWAL",
                "FIRST_DEFENDED_RETEST",
                "ENTRY",
            ],
            "target_policy": (
                "unconsumed liquidity objective after costs; no fallback target"
            ),
            "protective_fill_policy": (
                "if actual fill has crossed stop, cancel children and fail-close"
            ),
            "runner_snapshot": (
                "candidate-05@e9c858247ef5247bc3f4d8ad3f0de078a7ecebb0"
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
    args = parser.parse_args()
    result = run_stage(args)
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
