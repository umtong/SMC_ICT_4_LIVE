#!/usr/bin/env python3
"""CLI for the cross-market external-liquidity exhaustion system."""
from __future__ import annotations

import argparse
from datetime import date
import json
from pathlib import Path
import sys
from typing import Any

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
CANDIDATE21 = ROOT / "candidate-21"
CANDIDATE20 = ROOT / "candidate-20"
CANDIDATE19 = ROOT / "candidate-19"
CANDIDATE18 = ROOT / "candidate-18"
CANDIDATE17 = ROOT / "candidate-17"
CANDIDATE16 = ROOT / "candidate-16"
CANDIDATE05 = ROOT / "candidate-05"
for index, path in enumerate(
    (
        HERE,
        CANDIDATE21,
        CANDIDATE16,
        CANDIDATE20,
        CANDIDATE19,
        CANDIDATE18,
        CANDIDATE17,
        CANDIDATE05,
    ),
):
    sys.path.insert(index, str(path))

from timestamp_contract import install as install_timestamp_contract
from wrangler_contract import install as install_wrangler_contract
from positioning_contract import install as install_positioning_contract
from basis_contract import install as install_basis_contract
from book_depth_gap_contract import install as install_book_depth_gap_contract
from spot_participation_contract import install as install_spot_participation_contract

install_timestamp_contract()
install_wrangler_contract()
install_positioning_contract()
install_basis_contract()
install_book_depth_gap_contract()
install_spot_participation_contract()

from external_backtest import run_backtest
from smc_ict_4.manifest import write_json_atomic


def run_stage(args: argparse.Namespace) -> dict[str, Any]:
    result = run_backtest(
        config_path=args.config,
        build_start=date.fromisoformat(args.build_start),
        build_end=date.fromisoformat(args.build_end),
        evaluation_start=date.fromisoformat(args.evaluation_start),
        evaluation_end=date.fromisoformat(args.evaluation_end),
        cache=args.cache,
        output=args.output,
    )
    result["candidate"] = "external-liquidity-exhaustion-reversal"
    result["validation_mode"] = args.validation_mode
    result["external_alpha"] = (
        "synchronized spot-perpetual flow climax at external liquidity, "
        "followed by a strictly later microstructure and aggressor-flow reversal"
    )
    result["reused_engine"] = "NautilusTrader BacktestNode"
    result["reused_execution_clock"] = (
        "volume-preserving actual USD-M aggTrade windows from 1s through 16s"
    )
    result["reused_accounting"] = "Candidate 05 continuous NAV"
    write_json_atomic(args.output.resolve() / "metrics.json", result)
    write_json_atomic(
        args.output.resolve() / "spot_perp_contract.json",
        {
            "candidate": result["candidate"],
            "engine": "NautilusTrader BacktestNode",
            "data": [
                "checksum-verified Binance spot klines and aggTrades",
                "checksum-verified Binance USD-M klines, aggTrades, depth, metrics and premium",
            ],
            "parent": (
                "external-liquidity reach with synchronized spot/perpetual displacement, "
                "aggressor flow, volume climax, and low price efficiency"
            ),
            "router": ["EXTERNAL_LIQUIDITY_EXHAUSTION", "UNRESOLVED"],
            "transition": (
                "strictly later opposite microstructure break plus perpetual flow reversal"
            ),
            "entry": (
                "native Nautilus marketable LIMIT-GTD bracket at a volatility-aware "
                "adverse-fill price, accumulating recorded opposite-side aggTrade volume "
                "for at most fifteen seconds"
            ),
            "entry_integrity": (
                "fill fraction below 95%, fill beyond the adverse price limit, future data, "
                "order rejection, liquidation, or multiple concurrent intents invalidates the run"
            ),
            "execution_data": (
                "all actual futures aggTrades from 1.000s inclusive to 16.000s exclusive "
                "after every minute boundary; source price and quantity unchanged"
            ),
            "risk_fraction": 0.03,
            "continuous_nav": True,
            "custom_matching_or_accounting": False,
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
    stage.add_argument("--validation-mode", required=True)
    args = parser.parse_args()
    print(json.dumps(run_stage(args), indent=2, sort_keys=True, allow_nan=True))


if __name__ == "__main__":
    main()
