#!/usr/bin/env python3
"""Candidate 17 entry point; reuses Candidate 05's NautilusTrader runner."""
from __future__ import annotations

import argparse
from datetime import date
import json
from pathlib import Path
import sys
from typing import Any

HERE = Path(__file__).resolve().parent
CANDIDATE16 = HERE.parent / "candidate-16"
CANDIDATE05 = HERE.parent / "candidate-05"

# Candidate 16 must own the legacy top-level module name ``strategy`` because
# strategy_v2 imports Candidate16Config from it. Candidate 17 is exposed through
# the non-colliding ``candidate17_strategy`` adapter instead.
sys.path.insert(0, str(CANDIDATE16))
sys.path.insert(1, str(HERE))
sys.path.insert(2, str(CANDIDATE05))

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


def _candidate17_strategy_config(
    *,
    strategy_path: str,
    config_path: str,
    config: dict[str, Any],
):
    del strategy_path, config_path
    return _ORIGINAL_IMPORTABLE_STRATEGY_CONFIG(
        strategy_path="candidate17_strategy:Candidate17Strategy",
        config_path="candidate17_strategy:Candidate17Config",
        config=config,
    )


candidate05_backtest.ImportableStrategyConfig = _candidate17_strategy_config


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
    result["candidate"] = "candidate-17-remembered-defense-router"
    result["validation_mode"] = "pre-registered-untouched-week-screen"
    result["reused_runner"] = "research/candidate-05/backtest.py"
    result["reused_execution"] = "research/candidate-16/strategy_v2.py"
    result["strategy_path"] = "research/candidate-17/candidate17_strategy.py"
    result["strategy_implementation"] = "research/candidate-17/strategy.py"
    write_json_atomic(args.output.resolve() / "metrics.json", result)
    write_json_atomic(
        args.output.resolve() / "candidate17_contract.json",
        {
            "candidate": result["candidate"],
            "engine": "NautilusTrader BacktestNode",
            "risk_fraction": 0.03,
            "max_global_entry_or_position": 1,
            "decision_policy": {
                "clean_reversal": [
                    "FIRST_DEFENDED_INTERACTION",
                    "ZERO_OUTSIDE_CLOSES",
                    "BOUNDARY_RECLAIM",
                    "STRICTLY_LATER_OPPOSITE_INITIATIVE",
                ],
                "depletion_continuation": [
                    "REMEMBERED_DISPLAYED_DEFENSE",
                    "LATER_REATTACK_CLOSES_BEYOND_PARENT_EXTREME",
                    "DIRECTIONAL_FLOW_AND_RETURN",
                    "IMPACT_EFFICIENCY_IMPROVES_VS_FIRST_ATTACK",
                    "TOP_DEPTH_DEFENSE_WEAKENS",
                    "BROADER_DEPTH_AHEAD_WITHDRAWS",
                    "FRESH_OPEN_INTEREST_EXPANDS",
                    "FIRST_DEFENDED_RETEST",
                ],
                "otherwise": "UNRESOLVED_NO_TRADE",
            },
            "target_policy": (
                "unconsumed liquidity objective after costs; no fallback target"
            ),
            "protective_fill_policy": (
                "if actual fill has crossed stop, cancel children and fail-close"
            ),
            "positioning_feature": "causal oi_change_5m with metrics age <= 300 seconds",
            "strategy_import_adapter": (
                "candidate17_strategy preserves Candidate 16's top-level strategy module"
            ),
            "runner_snapshot": (
                "candidate-16-v2@0d43da0256af7d4d2a1aa81dcdb98fec8f625cda"
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
