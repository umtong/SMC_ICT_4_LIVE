#!/usr/bin/env python3
"""Candidate 20 entry point: Candidate 19 alpha on a sub-minute Nautilus clock."""
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

from smc_ict_4.manifest import write_json_atomic
from tick_backtest import run_backtest


def run_stage(args: argparse.Namespace) -> dict[str, Any]:
    result = run_backtest(
        config_path=args.config,
        build_start=date.fromisoformat(args.build_start),
        build_end=date.fromisoformat(args.build_end),
        evaluation_start=date.fromisoformat(args.evaluation_start),
        evaluation_end=date.fromisoformat(args.evaluation_end),
        cache=args.cache,
        output=args.output,
    )
    result["candidate"] = "candidate-20-actual-trade-execution-clock"
    result["validation_mode"] = args.validation_mode
    result["reused_alpha"] = "research/candidate-19/transmission_strategy.py"
    result["reused_execution"] = "research/candidate-18/fok_capped_strategy.py"
    result["reused_engine"] = "NautilusTrader BacktestNode"
    write_json_atomic(args.output.resolve() / "metrics.json", result)
    write_json_atomic(
        args.output.resolve() / "candidate20_contract.json",
        {
            "candidate": result["candidate"],
            "engine": "NautilusTrader BacktestNode",
            "alpha_change_from_candidate19": "NONE",
            "cost_change_from_candidate19": "NONE",
            "risk_change_from_candidate19": "NONE",
            "order_policy_change_from_candidate19": "NONE",
            "clock_change": (
                "already downloaded Binance aggTrades supply one actual trade "
                "event per minute after one second so native 250ms latency no "
                "longer degenerates into a full one-minute delay"
            ),
            "no_custom_matching_or_accounting": True,
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
