#!/usr/bin/env python3
"""Candidate 18 v10 entry point using the shared native TradeTick runner."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import candidate as candidate18_base
from smc_ict_4.manifest import write_json_atomic


def _quarter_hour_relaunch_strategy_config(
    *,
    strategy_path: str,
    config_path: str,
    config: dict[str, Any],
):
    del strategy_path, config_path
    return candidate18_base._ORIGINAL_IMPORTABLE_STRATEGY_CONFIG(
        strategy_path=(
            "candidate18_quarter_hour_relaunch_strategy:Candidate18Strategy"
        ),
        config_path=(
            "candidate18_quarter_hour_relaunch_strategy:Candidate18Config"
        ),
        config=config,
    )


def run_stage(args: argparse.Namespace) -> dict[str, Any]:
    candidate18_base.candidate05_backtest.ImportableStrategyConfig = (
        _quarter_hour_relaunch_strategy_config
    )
    result = candidate18_base.run_stage(args)
    result.update(
        {
            "candidate": "candidate-18-v10-quarter-hour-relaunch",
            "validation_mode": args.validation_mode,
            "strategy_path": (
                "research/candidate-18/"
                "candidate18_quarter_hour_relaunch_strategy.py"
            ),
            "strategy_implementation": (
                "research/candidate-18/quarter_hour_relaunch_strategy.py"
            ),
            "market_state": (
                "quarter-hour opening sponsorship, completed acceptance, "
                "defended retest, then a strictly later second auction leg"
            ),
            "execution_owner": (
                "candidate-18-v7 local twin-trigger TradeTick execution"
            ),
            "independent_family": True,
            "v9_policy_reused": (
                "opening context and defended-retest observation only; "
                "immediate retest entry removed"
            ),
        },
    )
    output = args.output.resolve()
    write_json_atomic(output / "metrics.json", result)
    contract_path = output / "candidate18_contract.json"
    contract = json.loads(contract_path.read_text(encoding="utf-8"))
    contract.update(
        {
            "candidate": result["candidate"],
            "version": "v10-quarter-hour-relaunch",
            "context": "UTC_QUARTER_HOUR_ONLY",
            "initiation": "FIRST_10S_SIGNED_FLOW_AND_NOTIONAL_BURST",
            "state": "COMPLETED_1M_ACCEPTANCE_BEYOND_PRIOR_3M_RANGE",
            "observation": "STRICTLY_LATER_DEFENDED_RETEST",
            "confirmation": "STILL_LATER_SECOND_LEG_RELAUNCH",
            "confirmation_inputs": (
                "new close beyond opening/retest extreme, tail and full "
                "aggressor flow, directional return, efficiency, queue"
            ),
            "pre_entry_invalidation": "ACCEPTED_RANGE_BOUNDARY_LOST",
            "post_entry_invalidation": (
                "OPPOSITE_PRE_EVENT_RANGE_BOUNDARY_LOST"
            ),
            "entry_execution": "BOUNDED_NON_CHASING_GTD_LIMIT",
            "risk": "CURRENT_NAV_TIMES_THREE_PERCENT_MAX_PLANNED_LOSS",
            "target": "NEXT_LIQUIDITY_OR_1P5R_WITHIN_SECOND_AUCTION_LEG",
            "v8_dependency": "NONE_ALPHA_POLICY_ONLY_V7_EXECUTION_REUSED",
        },
    )
    write_json_atomic(contract_path, contract)
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
