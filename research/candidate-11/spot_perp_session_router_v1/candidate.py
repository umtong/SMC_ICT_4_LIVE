#!/usr/bin/env python3
"""NautilusTrader runner for the spot/perpetual session router."""
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
        strategy_path="spot_perp_strategy:SpotPerpSessionStrategy",
        config_path="spot_perp_strategy:SpotPerpSessionConfig",
        config=config,
    )


candidate05_backtest.ImportableStrategyConfig = _strategy_config
candidate05_backtest.load_range = load_range


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
    result["candidate"] = "candidate-11-spot-perp-session-router-v1"
    result["validation_mode"] = args.validation_mode
    result["strategy_path"] = "spot_perp_strategy:SpotPerpSessionStrategy"
    result["reused_runner"] = "research/candidate-05/backtest.py"
    write_json_atomic(args.output.resolve() / "metrics.json", result)

    run_path = args.output.resolve() / "run.json"
    run_payload = json.loads(run_path.read_text(encoding="utf-8"))
    run_payload["candidate"] = result["candidate"]
    run_payload["spot_perp_session_router_v1"] = {
        "validation_mode": args.validation_mode,
        "strategy_path": result["strategy_path"],
        "spot_source": "Binance spot daily aggTrades",
        "perpetual_source": "Binance USD-M daily aggTrades",
        "l1_dataset_commit": candidate_v4.DATASET_COMMIT,
        "l1_dataset_sha256": candidate_v4.DATASET_SHA256,
    }
    write_json_atomic(run_path, run_payload)
    write_json_atomic(
        args.output.resolve() / "spot_perp_contract.json",
        {
            "candidate": result["candidate"],
            "engine": "NautilusTrader BacktestNode",
            "risk_fraction": 0.03,
            "max_global_entry_or_position": 1,
            "context": "paired completed session liquidity",
            "failed_auction_state": (
                "perpetual-only parent attack with attack-direction basis expansion, "
                "then spot rejection, reclaim, L1 flip, and later reversal initiative"
            ),
            "acceptance_state": (
                "broad spot/perpetual parent attack, later spot confirmation, "
                "outside residence, L1 persistence, and defended retest"
            ),
            "unresolved": "all other combinations",
            "execution_and_nav": "NautilusTrader",
        },
    )
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
    print(json.dumps(run_stage(args), indent=2, sort_keys=True, allow_nan=True))


if __name__ == "__main__":
    main()
