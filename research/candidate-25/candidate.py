#!/usr/bin/env python3
"""Candidate 25 entry point reusing Candidate 05's NautilusTrader runner."""
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


def _candidate25_strategy_config(
    *,
    strategy_path: str,
    config_path: str,
    config: dict[str, Any],
):
    del strategy_path, config_path
    return _ORIGINAL_IMPORTABLE_STRATEGY_CONFIG(
        strategy_path="candidate25_strategy:Candidate25Strategy",
        config_path="candidate25_strategy:Candidate25Config",
        config=config,
    )


candidate05_backtest.ImportableStrategyConfig = _candidate25_strategy_config


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
    result["candidate"] = "candidate-25-funding-window-reset-continuation"
    result["validation_mode"] = args.validation_mode
    result["reused_runner"] = "research/candidate-05/backtest.py"
    result["reused_execution"] = "research/candidate-18/fok_capped_strategy.py"
    result["reused_lifecycle"] = "research/candidate-19/transmission_strategy.py"
    result["strategy_path"] = "research/candidate-25/candidate25_strategy.py"
    write_json_atomic(args.output.resolve() / "metrics.json", result)
    write_json_atomic(
        args.output.resolve() / "candidate25_contract.json",
        {
            "candidate": result["candidate"],
            "engine": "NautilusTrader BacktestNode",
            "risk_fraction": 0.03,
            "max_global_entry_or_position": 1,
            "inherited_auction_families": "DISABLED",
            "seed_times_utc": ["07:45", "15:45", "23:45"],
            "scenario": [
                "FIRST_TEN_SECOND_IMBALANCE_OBSERVED_AFTER_MINUTE_CLOSE",
                "OPENING_NOTIONAL_ABOVE_CAUSAL_TRAILING_BASELINE",
                "SEED_IS_EVENT_NOT_ENTRY",
                "EXACTLY_THIRTY_LATER_COMPLETED_BARS",
                "PRICE_CLOSES_AGAINST_ORIGINAL_IMBALANCE",
                "COUNTERMOVE_EXTREME_DEFINES_INVALIDATION",
                "ENTER_ORIGINAL_IMBALANCE_DIRECTION_AFTER_FUNDING",
                "EXIT_BEFORE_NEXT_FUNDING_OR_NATIVE_STOP_TARGET",
            ],
            "entry_policy": "Candidate18 all-or-none price-capped FOK bracket",
            "risk_policy": "worst permissible fill including configured costs <= 3% NAV",
            "funding_policy": (
                "reuse inherited pre-funding flat; no custom funding accounting"
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
