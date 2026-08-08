#!/usr/bin/env python3
"""Candidate 22 entry point reusing Candidate 05's NautilusTrader runner."""
from __future__ import annotations

import argparse
from datetime import date
import json
from pathlib import Path
import sys
from typing import Any

HERE = Path(__file__).resolve().parent
CANDIDATE19 = HERE.parent / "candidate-19"
CANDIDATE18 = HERE.parent / "candidate-18"
CANDIDATE17 = HERE.parent / "candidate-17"
CANDIDATE16 = HERE.parent / "candidate-16"
CANDIDATE05 = HERE.parent / "candidate-05"

sys.path.insert(0, str(CANDIDATE16))
sys.path.insert(1, str(HERE))
sys.path.insert(2, str(CANDIDATE19))
sys.path.insert(3, str(CANDIDATE18))
sys.path.insert(4, str(CANDIDATE17))
sys.path.insert(5, str(CANDIDATE05))

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


def _candidate22_strategy_config(
    *,
    strategy_path: str,
    config_path: str,
    config: dict[str, Any],
):
    del strategy_path, config_path
    return _ORIGINAL_IMPORTABLE_STRATEGY_CONFIG(
        strategy_path="candidate22_strategy:Candidate22Strategy",
        config_path="candidate22_strategy:Candidate22Config",
        config=config,
    )


candidate05_backtest.ImportableStrategyConfig = _candidate22_strategy_config


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
    result["candidate"] = "candidate-22-compressed-session-expansion"
    result["validation_mode"] = args.validation_mode
    result["reused_runner"] = "research/candidate-05/backtest.py"
    result["reused_failed_auction_family"] = (
        "research/candidate-19/transmission_strategy.py"
    )
    result["reused_execution"] = "research/candidate-18/fok_capped_strategy.py"
    result["strategy_path"] = "research/candidate-22/candidate22_strategy.py"
    write_json_atomic(args.output.resolve() / "metrics.json", result)
    write_json_atomic(
        args.output.resolve() / "candidate22_contract.json",
        {
            "candidate": result["candidate"],
            "engine": "NautilusTrader BacktestNode",
            "risk_fraction": 0.03,
            "max_global_entry_or_position": 1,
            "scenario_families": {
                "failed_auction": "Candidate 19 unchanged and higher priority",
                "session_expansion": [
                    "FIRST_15_COMPLETED_BARS_DEFINE_FOUR_HOUR_OPENING_RANGE",
                    "RANGE_WIDTH_NOT_ABOVE_CAUSAL_MEDIAN_OF_PRIOR_SESSIONS",
                    "OUTSIDE_PROGRESS_WITH_DIRECTIONAL_FLOW_AND_RETURN",
                    "EFFICIENT_PRICE_DELIVERY_AND_ABNORMAL_NOTIONAL",
                    "FRESH_OPEN_INTEREST_EXPANSION",
                    "STRICTLY_LATER_FIRST_BOUNDARY_RETEST",
                    "BODY_REMAINS_OUTSIDE",
                    "TAIL_FLOW_NOT_ADVERSE",
                    "DISPLAYED_BOOK_SUPPORT_AND_LIQUIDITY_WITHDRAWAL",
                    "ONE_EPISODE_MAXIMUM_PER_SESSION",
                ],
            },
            "evidence_role_separation": {
                "breakout_state": [
                    "price",
                    "aggressor_flow",
                    "efficiency",
                    "notional",
                    "open_interest",
                ],
                "retest_confirmation": [
                    "first_touch",
                    "body_hold",
                    "tail_flow",
                    "displayed_book_imbalance",
                    "liquidity_withdrawal_ahead",
                ],
            },
            "entry_policy": "Candidate 18 all-or-none price-capped FOK bracket",
            "risk_policy": "worst permissible fill including configured costs <= 3% NAV",
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
