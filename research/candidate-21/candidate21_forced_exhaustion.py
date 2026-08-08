#!/usr/bin/env python3
"""Run Candidate 21 forced-flow exhaustion through NautilusTrader."""
from __future__ import annotations

import argparse
from datetime import date
import json
from pathlib import Path
import sys
from typing import Any

HERE = Path(__file__).resolve().parent
CANDIDATE20 = HERE.parent / "candidate-20"
CANDIDATE19 = HERE.parent / "candidate-19"
CANDIDATE18 = HERE.parent / "candidate-18"
CANDIDATE17 = HERE.parent / "candidate-17"
CANDIDATE16 = HERE.parent / "candidate-16"
CANDIDATE05 = HERE.parent / "candidate-05"

for path in (
    HERE,
    CANDIDATE20,
    CANDIDATE19,
    CANDIDATE18,
    CANDIDATE17,
    CANDIDATE16,
    CANDIDATE05,
):
    sys.path.insert(0, str(path))

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

import tick_backtest as runner
from smc_ict_4.manifest import write_json_atomic

_ORIGINAL_IMPORTABLE_STRATEGY_CONFIG = runner.ImportableStrategyConfig


def _forced_strategy_config(
    *,
    strategy_path: str,
    config_path: str,
    config: dict[str, Any],
):
    del strategy_path, config_path
    return _ORIGINAL_IMPORTABLE_STRATEGY_CONFIG(
        strategy_path=(
            "candidate21_forced_exhaustion_strategy:Candidate21ForcedStrategy"
        ),
        config_path=(
            "candidate21_forced_exhaustion_strategy:Candidate21ForcedConfig"
        ),
        config=config,
    )


runner.ImportableStrategyConfig = _forced_strategy_config


def _rewrite_run_manifest(output: Path) -> None:
    path = output.resolve() / "run.json"
    if not path.exists():
        return
    manifest = json.loads(path.read_text(encoding="utf-8"))
    manifest["candidate"] = "candidate-21-forced-flow-exhaustion"
    extra = dict(manifest.get("extra", {}))
    extra.update(
        {
            "strategy": "Candidate21ForcedStrategy",
            "engine": "NautilusTrader BacktestNode",
            "event": (
                "five-minute displacement plus notional burst, OI contraction "
                "and same-direction premium extension"
            ),
            "state_transition": (
                "later same-side aggression without price progress, then a "
                "strictly later opposite price/flow/book/premium reprice"
            ),
            "entry": "price-capped all-or-none FOK bracket",
            "target": "frozen pre-shock origin",
            "risk": "3% current continuous NAV planned loss",
            "execution_clock": "sparse actual aggregate trade plus 1m bars",
        },
    )
    manifest["extra"] = extra
    write_json_atomic(path, manifest)


def run_stage(args: argparse.Namespace) -> dict[str, Any]:
    result = runner.run_backtest(
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
            "candidate": "candidate-21-forced-flow-exhaustion",
            "validation_mode": args.validation_mode,
            "strategy": "Candidate21ForcedStrategy",
            "engine": "NautilusTrader BacktestNode",
            "alpha_family": "FORCED_POSITION_DELEVERAGING_EXHAUSTION",
            "entry_order": "LIMIT_FOK",
            "target_policy": "FROZEN_PRE_SHOCK_ORIGIN",
            "risk_fraction": 0.03,
        },
    )
    write_json_atomic(args.output.resolve() / "metrics.json", result)
    write_json_atomic(
        args.output.resolve() / "candidate21_forced_contract.json",
        {
            "candidate": result["candidate"],
            "engine": result["engine"],
            "no_custom_matching_or_accounting": True,
            "event_is_not_entry": True,
            "separate_exhaustion_and_reprice_observations": True,
            "natural_target": "pre-shock origin frozen before entry",
            "risk_fraction": 0.03,
            "one_instrument_entry_or_position": 1,
            "costs": "entry/stop fees plus adverse slippage in planned loss",
        },
    )
    _rewrite_run_manifest(args.output)
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
    print(json.dumps(result, indent=2, sort_keys=True, allow_nan=True))


if __name__ == "__main__":
    main()
