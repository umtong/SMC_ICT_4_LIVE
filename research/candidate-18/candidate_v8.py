#!/usr/bin/env python3
"""Candidate 18 v8 entry point using the shared native TradeTick runner."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import candidate as candidate18_base
from smc_ict_4.manifest import write_json_atomic


def _basis_strategy_config(
    *,
    strategy_path: str,
    config_path: str,
    config: dict[str, Any],
):
    del strategy_path, config_path
    return candidate18_base._ORIGINAL_IMPORTABLE_STRATEGY_CONFIG(
        strategy_path="candidate18_basis_strategy:Candidate18Strategy",
        config_path="candidate18_basis_strategy:Candidate18Config",
        config=config,
    )


def run_stage(args: argparse.Namespace) -> dict[str, Any]:
    candidate18_base.candidate05_backtest.ImportableStrategyConfig = (
        _basis_strategy_config
    )
    result = candidate18_base.run_stage(args)
    result.update(
        {
            "candidate": "candidate-18-v8-basis-dislocation-router",
            "validation_mode": args.validation_mode,
            "strategy_path": (
                "research/candidate-18/candidate18_basis_strategy.py"
            ),
            "strategy_implementation": (
                "research/candidate-18/basis_dislocation_strategy.py"
            ),
            "market_state": (
                "full-window failed auction plus opposing perpetual-index "
                "basis; immediate shock and generic acceptance are no-trade"
            ),
            "execution_owner": (
                "candidate-18-v7 local twin-trigger TradeTick execution"
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
            "version": "v8-basis-dislocation",
            "state_router": (
                "sustained failed auction is tradable only when side times "
                "fresh premium_index is negative"
            ),
            "shock_policy": "UNRESOLVED_NO_TRADE",
            "acceptance_policy": "SEPARATE_FAMILY_NOT_ACTIVE_IN_V8",
            "basis_threshold": "SIGN_ONLY_NO_FITTED_MAGNITUDE",
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
