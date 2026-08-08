#!/usr/bin/env python3
"""Candidate 16 v4 launcher on Candidate-05's NautilusTrader runner."""
from __future__ import annotations

import argparse
from datetime import date
import json
from pathlib import Path
from typing import Any

import candidate as candidate16_v2
import backtest as candidate05_backtest
from smc_ict_4.manifest import write_json_atomic

_ORIGINAL_IMPORTABLE_STRATEGY_CONFIG = (
    candidate16_v2._ORIGINAL_IMPORTABLE_STRATEGY_CONFIG
)
_ACTIVE_MODE = "combined"


def _candidate16_v4_strategy_config(
    *,
    strategy_path: str,
    config_path: str,
    config: dict[str, Any],
):
    del strategy_path, config_path
    strategy = (
        "strategy_v4:BaselinePositioningResetStrategy"
        if _ACTIVE_MODE == "baseline"
        else "strategy_v4:Candidate16V4Strategy"
    )
    return _ORIGINAL_IMPORTABLE_STRATEGY_CONFIG(
        strategy_path=strategy,
        config_path="strategy_v4:Candidate16V4Config",
        config=config,
    )


candidate05_backtest.ImportableStrategyConfig = _candidate16_v4_strategy_config


def run_stage(args: argparse.Namespace) -> dict[str, Any]:
    global _ACTIVE_MODE
    _ACTIVE_MODE = args.mode
    result = candidate05_backtest.run_backtest(
        config_path=args.config,
        build_start=date.fromisoformat(args.build_start),
        build_end=date.fromisoformat(args.build_end),
        evaluation_start=date.fromisoformat(args.evaluation_start),
        evaluation_end=date.fromisoformat(args.evaluation_end),
        cache=args.cache,
        output=args.output,
    )
    result["candidate"] = (
        "candidate-16-v4-v39-baseline"
        if args.mode == "baseline"
        else "candidate-16-v4-v39-plus-forced-delivery"
    )
    result["mode"] = args.mode
    result["reused_runner"] = "research/candidate-05/backtest.py"
    result["reused_baseline"] = (
        "research/candidate-05/strategy_v39_positioning_reset.py"
    )
    result["added_strategy"] = (
        None if args.mode == "baseline" else "research/candidate-16/strategy_v4.py"
    )
    write_json_atomic(args.output.resolve() / "metrics.json", result)
    write_json_atomic(
        args.output.resolve() / "candidate16_contract.json",
        {
            "candidate": result["candidate"],
            "mode": args.mode,
            "research_stage": "DEVELOPMENT",
            "engine": "NautilusTrader BacktestNode",
            "risk_fraction": 0.03,
            "max_global_entry_or_position": 1,
            "baseline_policy": "Candidate-05 v39 unchanged",
            "added_family": (
                None
                if args.mode == "baseline"
                else [
                    "five-minute price shock with OI contraction and premium displacement",
                    "later same-direction aggressor flow and efficient progress",
                    "later withdrawal of displayed liquidity ahead",
                    "later directional price trigger",
                ]
            ),
            "explicit_no_trade": (
                "no reversal when forced delivery does not persist"
            ),
            "target_policy": (
                "pre-event live confirmed liquidity only; no synthetic fallback"
            ),
            "invalidation": "later confirmation-leg opposite extreme",
            "runner_snapshot": "candidate-05@candidate-16 branch snapshot",
        },
    )
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    stage = sub.add_parser("stage")
    stage.add_argument(
        "--mode",
        choices=("baseline", "combined"),
        default="combined",
    )
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
