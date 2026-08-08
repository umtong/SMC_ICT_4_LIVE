#!/usr/bin/env python3
"""Candidate 16 v6 entry point; reuse fixed L1 data and Candidate 05 runner."""
from __future__ import annotations

import argparse
from datetime import date
import json
from pathlib import Path
from typing import Any

import candidate_v4 as v4_runner
import backtest as candidate05_backtest
from features_v4 import DATASET_COMMIT, DATASET_SHA256, DATASET_URL
from features_v4b import load_range as load_range_v4b
from smc_ict_4.manifest import write_json_atomic


_ORIGINAL_IMPORTABLE_STRATEGY_CONFIG = (
    v4_runner._ORIGINAL_IMPORTABLE_STRATEGY_CONFIG
)


def _candidate16_v6_strategy_config(
    *,
    strategy_path: str,
    config_path: str,
    config: dict[str, Any],
):
    del strategy_path, config_path
    return _ORIGINAL_IMPORTABLE_STRATEGY_CONFIG(
        strategy_path="strategy_v6:Candidate16V6Strategy",
        config_path="strategy_v6:Candidate16V6Config",
        config=config,
    )


candidate05_backtest.ImportableStrategyConfig = _candidate16_v6_strategy_config
candidate05_backtest.load_range = load_range_v4b
candidate05_backtest.prepare_catalog = v4_runner._prepare_catalog_v4


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
    candidate = "candidate-16-v6-informed-initiative-continuation"
    result["candidate"] = candidate
    result["validation_mode"] = "pre_registered_screen"
    result["reused_runner"] = "research/candidate-05/backtest.py"
    result["strategy_path"] = "research/candidate-16/strategy_v6.py"
    result["feature_path"] = "research/candidate-16/features_v4b.py"
    result["l1_dataset_commit"] = DATASET_COMMIT
    result["l1_dataset_sha256"] = DATASET_SHA256
    write_json_atomic(args.output.resolve() / "metrics.json", result)

    run_path = args.output.resolve() / "run.json"
    run_payload = json.loads(run_path.read_text(encoding="utf-8"))
    run_payload["candidate"] = candidate
    run_payload["candidate16_v6"] = {
        "validation_mode": result["validation_mode"],
        "strategy_path": result["strategy_path"],
        "feature_path": result["feature_path"],
        "dataset_commit": DATASET_COMMIT,
        "dataset_sha256": DATASET_SHA256,
        "economic_hypothesis": (
            "cost-exceeding new-OI impulse with aligned aggressor and L1 "
            "pressure, followed by midpoint-held pullback and later boundary "
            "break as a new continuation leg"
        ),
    }
    write_json_atomic(run_path, run_payload)

    write_json_atomic(
        args.output.resolve() / "candidate16_v6_contract.json",
        {
            "candidate": candidate,
            "engine": "NautilusTrader BacktestNode",
            "risk_fraction": 0.03,
            "max_global_entry_or_position": 1,
            "sequence": [
                "COST_EXCEEDING_INITIATIVE",
                "ALIGNED_AGGRESSOR_FLOW",
                "POSITIVE_OPEN_INTEREST_CHANGE",
                "ALIGNED_CLOSING_L1_PRESSURE",
                "STATE_FROZEN_NO_ORDER",
                "COUNTER_BAR_HOLDS_INITIATIVE_MIDPOINT",
                "STRICTLY_LATER_PRICE_FLOW_L1_BREAKS_PULLBACK_BOUNDARY",
                "FOK_PRICE_CAPPED_ENTRY",
                "PULLBACK_EXTREME_INVALIDATION",
                "PRE_EXISTING_LIQUIDITY_OBJECTIVE",
            ],
            "state_entry_role_separation": {
                "state": "initiative plus OI plus L1 alignment",
                "transition": "counter bar holds midpoint",
                "entry_confirmation": (
                    "later pullback-boundary break plus renewed flow and L1"
                ),
            },
            "target_policy": (
                "nearest already-active directional liquidity pool satisfying "
                "cost-aware minimum R; no fallback target"
            ),
            "entry_policy": (
                "FOK LIMIT at worst permissible cap; full fill or no position"
            ),
            "risk_policy": (
                "3% current account NAV at worst fill, pullback stop, fees and "
                "adverse slippage"
            ),
            "candidate05_runner_snapshot": (
                "e9c858247ef5247bc3f4d8ad3f0de078a7ecebb0"
            ),
            "dataset_commit": DATASET_COMMIT,
            "dataset_sha256": DATASET_SHA256,
            "dataset_url": DATASET_URL,
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
