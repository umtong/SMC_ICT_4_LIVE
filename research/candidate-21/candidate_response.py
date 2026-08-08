#!/usr/bin/env python3
"""Candidate 21 same-minute response command-line entry point."""
from __future__ import annotations

import argparse
from datetime import date
import json
from pathlib import Path
import sys
from typing import Any

HERE = Path(__file__).resolve().parent
CANDIDATE20 = HERE.parent / "candidate-20"
CANDIDATE19 = HERE.parent / "candidate-19"
CANDIDATE18 = HERE.parent / "candidate-18"
CANDIDATE17 = HERE.parent / "candidate-17"
CANDIDATE16 = HERE.parent / "candidate-16"
CANDIDATE05 = HERE.parent / "candidate-05"

for index, path in enumerate((
    HERE,
    CANDIDATE16,
    CANDIDATE20,
    CANDIDATE19,
    CANDIDATE18,
    CANDIDATE17,
    CANDIDATE05,
)):
    sys.path.insert(index, str(path))

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

from candidate21_response_backtest import run_backtest
from smc_ict_4.manifest import write_json_atomic


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
    result["candidate"] = "candidate-21-same-minute-response-router"
    result["validation_mode"] = args.validation_mode
    result["reused_data_and_accounting"] = "research/candidate-05"
    result["reused_execution_clock"] = "research/candidate-20/tick_backtest.py"
    result["reused_order_lifecycle"] = "research/candidate-18/fok_capped_strategy.py"
    result["new_alpha"] = (
        "first-10-second shock separated from strictly later 10-60-second response"
    )
    result["reused_engine"] = "NautilusTrader BacktestNode"
    write_json_atomic(args.output.resolve() / "metrics.json", result)
    write_json_atomic(
        args.output.resolve() / "candidate21_response_contract.json",
        {
            "candidate": result["candidate"],
            "engine": "NautilusTrader BacktestNode",
            "parent_event": "lagged-phase-normalized first-10-second boundary shock",
            "state_router": "non-overlapping 10-60-second price/flow/book response",
            "classification_thresholds": "sign coherence only; no fitted magnitude cutoffs",
            "branches": ["ACCEPTANCE", "FAILED_AUCTION", "UNRESOLVED"],
            "entry": "all-or-none price-capped FOK bracket at completed minute",
            "risk_fraction": 0.03,
            "continuous_nav": True,
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
    print(json.dumps(result, indent=2, sort_keys=True, allow_nan=True))


if __name__ == "__main__":
    main()
