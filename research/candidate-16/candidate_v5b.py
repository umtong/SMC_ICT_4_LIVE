#!/usr/bin/env python3
"""Candidate 16 v5b runner: frozen v5 economics with FOK price-cap repair."""
from __future__ import annotations

import argparse
from datetime import date
import json
from pathlib import Path
from typing import Any

import candidate_v5 as v5_runner
import backtest as candidate05_backtest
from features_v4 import DATASET_COMMIT, DATASET_SHA256, DATASET_URL
from features_v4b import load_range as load_range_v4b
from smc_ict_4.manifest import write_json_atomic


_ORIGINAL_IMPORTABLE_STRATEGY_CONFIG = (
    v5_runner._ORIGINAL_IMPORTABLE_STRATEGY_CONFIG
)


def _candidate16_v5b_strategy_config(
    *,
    strategy_path: str,
    config_path: str,
    config: dict[str, Any],
):
    del strategy_path, config_path
    return _ORIGINAL_IMPORTABLE_STRATEGY_CONFIG(
        strategy_path="strategy_v5b:Candidate16V5BStrategy",
        config_path="strategy_v5b:Candidate16V5Config",
        config=config,
    )


candidate05_backtest.ImportableStrategyConfig = _candidate16_v5b_strategy_config
candidate05_backtest.load_range = load_range_v4b


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
    candidate = "candidate-16-v5b-crowded-initiative-fok-price-cap"
    result["candidate"] = candidate
    result["validation_mode"] = "same_pre_registered_period_implementation_repair"
    result["strategy_path"] = "research/candidate-16/strategy_v5b.py"
    result["feature_path"] = "research/candidate-16/features_v4b.py"
    result["implementation_repair"] = {
        "economic_state_changed": False,
        "direction_changed": False,
        "stop_changed": False,
        "target_changed": False,
        "worst_fill_cap_changed": False,
        "from": "STOP_LIMIT trigger vulnerable to completed-bar replay race",
        "to": "all-or-none FOK LIMIT at identical worst-fill cap",
    }
    write_json_atomic(args.output.resolve() / "metrics.json", result)

    run_path = args.output.resolve() / "run.json"
    run_payload = json.loads(run_path.read_text(encoding="utf-8"))
    run_payload["candidate"] = candidate
    run_payload["candidate16_v5b"] = {
        "validation_mode": result["validation_mode"],
        "strategy_path": result["strategy_path"],
        "dataset_commit": DATASET_COMMIT,
        "dataset_sha256": DATASET_SHA256,
        "implementation_repair": result["implementation_repair"],
    }
    write_json_atomic(run_path, run_payload)

    write_json_atomic(
        args.output.resolve() / "candidate16_v5b_contract.json",
        {
            "candidate": candidate,
            "engine": "NautilusTrader BacktestNode",
            "risk_fraction": 0.03,
            "max_global_entry_or_position": 1,
            "economic_system": "identical to candidate-16 v5",
            "evaluation": ["2023-06-05", "2023-06-11"],
            "implementation_failure_observed": (
                "completed-bar STOP_LIMIT trigger was already in the market on "
                "the next replayed event"
            ),
            "repair": (
                "LIMIT parent at the identical v5 worst-fill cap with FOK; full "
                "fill at or better than cap or no position"
            ),
            "preserved": [
                "crowded initiative state",
                "strictly later price-flow-L1 failure",
                "direction",
                "shock-extreme stop",
                "shock-origin or active-liquidity target",
                "cost model",
                "3% worst-fill risk",
                "performance gate",
                "pre-registered dates",
            ],
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
