#!/usr/bin/env python3
"""Candidate 21 state-resolved flow-sweep entry point."""
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

for index, path in enumerate(
    (
        HERE,
        CANDIDATE16,
        CANDIDATE20,
        CANDIDATE19,
        CANDIDATE18,
        CANDIDATE17,
        CANDIDATE05,
    ),
):
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

from candidate21_flow_state_backtest import run_backtest
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
    result["candidate"] = "candidate-21-state-resolved-flow-sweep"
    result["validation_mode"] = args.validation_mode
    result["new_state_router"] = (
        "persistent sponsorship OR delayed efficiency release; "
        "decayed effort closes no-trade"
    )
    result["new_state_numeric_thresholds"] = None
    result["reused_execution"] = (
        "research/candidate-21/candidate21_flow_sweep_strategy.py"
    )
    result["reused_engine"] = "NautilusTrader BacktestNode"
    write_json_atomic(args.output.resolve() / "metrics.json", result)
    write_json_atomic(
        args.output.resolve() / "candidate21_flow_state_contract.json",
        {
            "candidate": result["candidate"],
            "engine": "NautilusTrader BacktestNode",
            "parent_event_change_from_flow_sweep": "NONE",
            "one_and_three_hour_context_change": "NONE",
            "stop_change": "NONE",
            "risk_change": "NONE",
            "exit_horizon_change": "NONE",
            "execution_change": "NONE",
            "new_state_transition": (
                "response normalized directional flow >= parent directional "
                "flow OR response price efficiency and directional return "
                "both strictly improve"
            ),
            "new_numeric_thresholds": None,
            "decay_state": "UNRESOLVED_NO_TRADE",
            "entry": "market GTC across successive external 10-second bars",
            "protective_stop": "full planned reduce-only stop-market",
            "target": None,
            "risk_fraction": 0.03,
            "continuous_nav": True,
            "fees_and_slippage": True,
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
