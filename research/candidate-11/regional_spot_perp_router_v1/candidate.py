#!/usr/bin/env python3
"""NautilusTrader runner for the regional spot/perpetual/L1 router."""
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
        strategy_path="regional_strategy:RegionalSpotPerpStrategy",
        config_path="regional_strategy:RegionalSpotPerpConfig",
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
    result.update(
        {
            "candidate": "candidate-11-regional-spot-perp-router-v1",
            "validation_mode": args.validation_mode,
            "strategy_path": "regional_strategy:RegionalSpotPerpStrategy",
            "reused_runner": "research/candidate-05/backtest.py",
        },
    )
    write_json_atomic(args.output.resolve() / "metrics.json", result)
    run_path = args.output.resolve() / "run.json"
    payload = json.loads(run_path.read_text(encoding="utf-8"))
    payload["candidate"] = result["candidate"]
    payload["regional_spot_perp_router_v1"] = {
        "validation_mode": args.validation_mode,
        "liquidity": "completed named regional ranges",
        "latent_state": "spot/perpetual participation and basis",
        "execution_confirmation": "strictly later L1 pressure or reversal initiative",
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
