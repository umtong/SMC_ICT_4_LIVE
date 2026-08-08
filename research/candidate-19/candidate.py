#!/usr/bin/env python3
"""Candidate 19 entry point reusing Candidate 05's NautilusTrader runner."""
from __future__ import annotations

import argparse
from datetime import date
import json
from pathlib import Path
import sys
from typing import Any

HERE = Path(__file__).resolve().parent
CANDIDATE18 = HERE.parent / "candidate-18"
CANDIDATE17 = HERE.parent / "candidate-17"
CANDIDATE16 = HERE.parent / "candidate-16"
CANDIDATE05 = HERE.parent / "candidate-05"

sys.path.insert(0, str(CANDIDATE16))
sys.path.insert(1, str(HERE))
sys.path.insert(2, str(CANDIDATE18))
sys.path.insert(3, str(CANDIDATE17))
sys.path.insert(4, str(CANDIDATE05))

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


def _candidate19_strategy_config(
    *,
    strategy_path: str,
    config_path: str,
    config: dict[str, Any],
):
    del strategy_path, config_path
    return _ORIGINAL_IMPORTABLE_STRATEGY_CONFIG(
        strategy_path="candidate19_strategy:Candidate19Strategy",
        config_path="candidate19_strategy:Candidate19Config",
        config=config,
    )


candidate05_backtest.ImportableStrategyConfig = _candidate19_strategy_config


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
    result["candidate"] = "candidate-19-shock-transmission-fok"
    result["validation_mode"] = args.validation_mode
    result["reused_runner"] = "research/candidate-05/backtest.py"
    result["reused_execution"] = "research/candidate-18/fok_capped_strategy.py"
    result["reused_state"] = "research/candidate-18/execution_preserving_strategy.py"
    result["strategy_path"] = "research/candidate-19/candidate19_strategy.py"
    write_json_atomic(args.output.resolve() / "metrics.json", result)
    write_json_atomic(
        args.output.resolve() / "candidate19_contract.json",
        {
            "candidate": result["candidate"],
            "engine": "NautilusTrader BacktestNode",
            "risk_fraction": 0.03,
            "max_global_entry_or_position": 1,
            "shock_policy": [
                "IMMEDIATE_NOTIONAL_SHOCK_ARMS_STATE_ONLY",
                "STRICTLY_LATER_OUTSIDE_CLOSE",
                "CUMULATIVE_PROGRESS_BEYOND_SHOCK_CLOSE",
                "SAME_SIDE_FLOW_RETURN_AND_QUEUE_SUPPORT",
                "DISPLAYED_LIQUIDITY_WITHDRAWAL_AHEAD",
                "AGGRESSIVE_FLOW_WITHOUT_PROGRESS_IS_ABSORPTION_NO_TRADE",
            ],
            "sustained_policy": "REUSE_CANDIDATE18_FULL_WINDOW_INITIATIVE",
            "entry_policy": "CANDIDATE18_ALL_OR_NONE_PRICE_CAPPED_FOK_BRACKET",
            "threshold_policy": (
                "remaining registered initiative window and directional signs only; "
                "no PnL-fitted shock transmission threshold"
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
