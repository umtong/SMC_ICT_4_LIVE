#!/usr/bin/env python3
"""Candidate 16 v3 entry point; reuses Candidate 03 data and Candidate 05 NT."""
from __future__ import annotations

import argparse
from datetime import date
import json
from pathlib import Path
import sys
from typing import Any

HERE = Path(__file__).resolve().parent
CANDIDATE05 = HERE.parent / "candidate-05"
CANDIDATE03 = HERE.parent / "candidate-03"
sys.path.insert(0, str(HERE))
sys.path.insert(1, str(CANDIDATE05))
sys.path.insert(2, str(CANDIDATE03))

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

import backtest as candidate05_backtest
from features_v3 import load_range as load_range_v3
from smc_ict_4.manifest import write_json_atomic

_ORIGINAL_IMPORTABLE_STRATEGY_CONFIG = candidate05_backtest.ImportableStrategyConfig
_ORIGINAL_PREPARE_CATALOG = candidate05_backtest.prepare_catalog


def _candidate16_v3_strategy_config(
    *,
    strategy_path: str,
    config_path: str,
    config: dict[str, Any],
):
    del strategy_path, config_path
    return _ORIGINAL_IMPORTABLE_STRATEGY_CONFIG(
        strategy_path="strategy_v3:Candidate16V3Strategy",
        config_path="strategy_v3:Candidate16V3Config",
        config=config,
    )


def _prepare_catalog_v3(**kwargs):
    instrument, manifest_path = _ORIGINAL_PREPARE_CATALOG(**kwargs)
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    payload["candidate16_v3_data_extension"] = {
        "book_ticker_source": (
            "research/candidate-03/nt_lvcfr_data.py"
        ),
        "feature_builder": (
            "research/candidate-16/topbook_features.py"
        ),
        "meaning": (
            "actual best-bid/best-ask event sequence aggregated only "
            "after each completed minute"
        ),
        "not_used_as": [
            "matching engine",
            "fill simulator",
            "accounting engine",
        ],
    }
    manifest_path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return instrument, manifest_path


candidate05_backtest.ImportableStrategyConfig = _candidate16_v3_strategy_config
candidate05_backtest.load_range = load_range_v3
candidate05_backtest.prepare_catalog = _prepare_catalog_v3


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
    candidate = "candidate-16-v3-top-of-book-resiliency"
    result["candidate"] = candidate
    result["validation_mode"] = "screen"
    result["reused_runner"] = "research/candidate-05/backtest.py"
    result["reused_book_ticker"] = (
        "research/candidate-03/nt_lvcfr_data.py"
    )
    result["strategy_path"] = "research/candidate-16/strategy_v3.py"
    write_json_atomic(args.output.resolve() / "metrics.json", result)

    run_path = args.output.resolve() / "run.json"
    run_payload = json.loads(run_path.read_text(encoding="utf-8"))
    run_payload["candidate"] = candidate
    run_payload["candidate16_v3"] = {
        "validation_mode": "screen",
        "strategy_path": result["strategy_path"],
        "book_ticker_source": result["reused_book_ticker"],
    }
    write_json_atomic(run_path, run_payload)

    write_json_atomic(
        args.output.resolve() / "candidate16_v3_contract.json",
        {
            "candidate": candidate,
            "engine": "NautilusTrader BacktestNode",
            "risk_fraction": 0.03,
            "max_global_entry_or_position": 1,
            "data_roles": {
                "parent_state": "completed bars plus aggregate trades",
                "liquidity_response": "actual Binance bookTicker best quotes",
                "execution_and_nav": "NautilusTrader",
            },
            "failure_sequence": [
                "PARENT_ATTACK",
                "BEST_QUOTE_DEFENSE_AND_SPREAD_RECOVERY",
                "COMPLETED_RECLAIM",
                "FAILURE_FROZEN_NO_ORDER",
                "LATER_TRADE_FLOW_MIDPOINT_AND_QUEUE_INITIATIVE",
                "ENTRY",
            ],
            "acceptance_sequence": [
                "OUTSIDE_RESIDENCE",
                "BEST_QUOTE_WITHDRAWAL_AHEAD",
                "MIDPOINT_IMPACT_RETENTION",
                "FIRST_DEFENDED_RETEST",
                "ENTRY",
            ],
            "target_policy": (
                "unconsumed liquidity objective after costs; no fallback target"
            ),
            "protective_fill_policy": (
                "if actual fill has crossed stop, cancel children and fail-close"
            ),
            "candidate05_runner_snapshot": (
                "e9c858247ef5247bc3f4d8ad3f0de078a7ecebb0"
            ),
            "candidate03_book_ticker_contract": (
                "research/candidate-03/nt_lvcfr_data.py"
            ),
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
