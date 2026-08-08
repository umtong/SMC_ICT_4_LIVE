#!/usr/bin/env python3
"""NautilusTrader runner for spot/perpetual liquidity-vacuum continuation."""
from __future__ import annotations

import argparse
from datetime import date
import json
from pathlib import Path
from typing import Any

import candidate_v4
import backtest as candidate05_backtest
from spot_perp_features import load_range
from smc_ict_4.manifest import write_json_atomic


def _strategy_config(*, strategy_path: str, config_path: str, config: dict[str, Any]):
    del strategy_path, config_path
    return candidate_v4._ORIGINAL_IMPORTABLE_STRATEGY_CONFIG(
        strategy_path="vacuum_strategy:SpotPerpVacuumStrategy",
        config_path="vacuum_strategy:SpotPerpVacuumConfig",
        config=config,
    )


candidate05_backtest.ImportableStrategyConfig = _strategy_config
candidate05_backtest.load_range = load_range


def run(args: argparse.Namespace) -> dict[str, Any]:
    result = candidate05_backtest.run_backtest(
        config_path=args.config,
        build_start=date.fromisoformat(args.build_start),
        build_end=date.fromisoformat(args.build_end),
        evaluation_start=date.fromisoformat(args.evaluation_start),
        evaluation_end=date.fromisoformat(args.evaluation_end),
        cache=args.cache,
        output=args.output,
    )
    result["candidate"] = "candidate-11-spot-perp-vacuum-continuation-v1"
    result["validation_mode"] = args.validation_mode
    result["strategy_path"] = "vacuum_strategy:SpotPerpVacuumStrategy"
    result["reused_runner"] = "research/candidate-05/backtest.py"
    write_json_atomic(args.output.resolve() / "metrics.json", result)
    run_path = args.output.resolve() / "run.json"
    payload = json.loads(run_path.read_text(encoding="utf-8"))
    payload["candidate"] = result["candidate"]
    payload["spot_perp_vacuum_continuation_v1"] = {
        "validation_mode": args.validation_mode,
        "state_definition": "interaction-minute broad spot/perpetual initiative",
        "transition_confirmation": "first strictly later minute spot/perp/L1 persistence",
        "entry": "confirmation close via NautilusTrader",
        "risk_fraction": 0.03,
        "global_entry_or_position_limit": 1,
    }
    write_json_atomic(run_path, payload)
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--build-start", required=True)
    parser.add_argument("--build-end", required=True)
    parser.add_argument("--evaluation-start", required=True)
    parser.add_argument("--evaluation-end", required=True)
    parser.add_argument("--cache", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--validation-mode", required=True)
    args = parser.parse_args()
    print(json.dumps(run(args), indent=2, sort_keys=True, allow_nan=True))


if __name__ == "__main__":
    main()
