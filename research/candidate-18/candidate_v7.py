#!/usr/bin/env python3
"""Candidate 18 v7 entry point over the shared Candidate 18 TradeTick runner."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import candidate as candidate18_base
from smc_ict_4.manifest import write_json_atomic


def run_stage(args: argparse.Namespace) -> dict[str, Any]:
    result = candidate18_base.run_stage(args)
    result.update(
        {
            "candidate": "candidate-18-v7-local-twin-trigger-router",
            "validation_mode": args.validation_mode,
            "strategy_path": "research/candidate-18/candidate18_strategy.py",
            "strategy_implementation": (
                "research/candidate-18/local_twin_trigger_strategy.py"
            ),
            "entry_execution": (
                "one non-chasing price-capped GTD LIMIT with a bounded "
                "micro-auction fill window"
            ),
            "protective_trigger": (
                "local LAST_PRICE STOP_MARKET and MARKET_IF_TOUCHED; "
                "only the touched exit is released as native MARKET"
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
            "version": "v7-local-twin-trigger",
            "protection": (
                "every actual fill receives independent reduce-only "
                "LAST_PRICE-emulated STOP_MARKET and MARKET_IF_TOUCHED exits"
            ),
            "stop_release": (
                "actual TradeTick crossing releases only the touched exit "
                "as native MARKET through configured latency"
            ),
            "untouched_sibling": (
                "remains inside NautilusTrader order emulation and is canceled "
                "without a venue-side reduce-only race"
            ),
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
