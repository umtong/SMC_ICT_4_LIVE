#!/usr/bin/env python3
"""Candidate 16 v7: run the previously untested v52 residual strategy.

Candidate 05 already implemented the economic strategy, one-account four-symbol
runner, causal feature contracts, and audited global entry lifecycle.  Its v52
workflow never reached a backtest because the workflow overwrote the v36 module
and removed an imported mixin.  This adapter repairs only strategy registration:

- retain the original ``CrossSectionalResidualStrategy`` byte-for-byte;
- compose it with the existing strict shared-account entry lifecycle;
- expose one importable class per project symbol;
- route the existing shared NautilusTrader runner to those classes.

No matching, fills, positions, fees, margin, liquidation, portfolio or NAV logic
is implemented here.
"""
from __future__ import annotations

import argparse
from datetime import date
import json
from pathlib import Path
from typing import Any

from global_entry_slot_v4 import FINAL_SHARED_ACCOUNT_ENTRY_COORDINATOR
import strategy_global_slot_wrappers_v4 as shared_v4

# Importing v5 deliberately installs the repository's final strict coordinator
# into ``strategy_global_slot_wrappers_v4`` before the v52 composition exists.
import strategy_global_slot_wrappers_v5 as shared_v5  # noqa: F401
from strategy_v52_cross_sectional_residual import CrossSectionalResidualStrategy


V7_WINNER = (
    "strategy_v52_cross_sectional_residual:CrossSectionalResidualStrategy"
)
PROJECT_SYMBOLS = ("BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT")


if shared_v4.SHARED_ACCOUNT_ENTRY_COORDINATOR is not FINAL_SHARED_ACCOUNT_ENTRY_COORDINATOR:
    raise RuntimeError("final shared-account coordinator was not installed")


class Candidate16V7SharedResidualStrategy(
    shared_v4.SharedAccountEntryLifecycleMixin,
    CrossSectionalResidualStrategy,
):
    """Original v52 economics plus the audited one-global-slot lifecycle."""


class Candidate16V7BTCUSDTStrategy(Candidate16V7SharedResidualStrategy):
    pass


class Candidate16V7ETHUSDTStrategy(Candidate16V7SharedResidualStrategy):
    pass


class Candidate16V7SOLUSDTStrategy(Candidate16V7SharedResidualStrategy):
    pass


class Candidate16V7XRPUSDTStrategy(Candidate16V7SharedResidualStrategy):
    pass


def candidate16_v7_strategy_path(winner: str, symbol: str) -> str:
    """Return the import path accepted by the frozen shared-account runner."""
    if winner != V7_WINNER:
        raise ValueError(f"unsupported Candidate 16 v7 winner: {winner}")
    if symbol not in PROJECT_SYMBOLS:
        raise ValueError(f"unsupported project symbol: {symbol}")
    return f"candidate_v7:Candidate16V7{symbol}Strategy"


def install_shared_adapter():
    """Install the registration repair, then return the existing runner module."""
    import shared_account_strategy_variants_v2 as variants
    import shared_account_backtest_v2 as runner

    variants.WINNER_TO_FAMILY[V7_WINNER] = "v52"
    variants.final_shared_strategy_path = candidate16_v7_strategy_path
    runner._base.final_shared_strategy_path = candidate16_v7_strategy_path
    return runner


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True, allow_nan=True) + "\n",
        encoding="utf-8",
    )


def run_stage(args: argparse.Namespace) -> dict[str, Any]:
    runner = install_shared_adapter()
    runner.reset_shared_cross_asset_context()
    runner.reset_shared_smt_session_context()

    metrics = runner._base.run_shared_account(
        winner_evidence_path=args.winner_evidence,
        build_start=date.fromisoformat(args.build_start),
        build_end=date.fromisoformat(args.build_end),
        evaluation_start=date.fromisoformat(args.evaluation_start),
        evaluation_end=date.fromisoformat(args.evaluation_end),
        cache_root=args.cache,
        output=args.output,
    )
    candidate = "candidate-16-v7-cross-sectional-residual"
    metrics["candidate"] = candidate
    metrics["validation_mode"] = "pre_registered_one_account_four_symbol_screen"
    metrics["strategy_source"] = (
        "research/candidate-05/strategy_v52_cross_sectional_residual.py"
    )
    metrics["adapter_source"] = "research/candidate-16/candidate_v7.py"
    write_json(args.output.resolve() / "metrics.json", metrics)

    run_path = args.output.resolve() / "run.json"
    run_payload = json.loads(run_path.read_text(encoding="utf-8"))
    run_payload["candidate"] = candidate
    run_payload["candidate16_v7"] = {
        "economic_strategy_changed": False,
        "shared_account_runner_changed": False,
        "registration_repair_only": True,
        "strategy_source": metrics["strategy_source"],
        "adapter_source": metrics["adapter_source"],
        "winner": V7_WINNER,
    }
    write_json(run_path, run_payload)

    write_json(
        args.output.resolve() / "candidate16_v7_contract.json",
        {
            "candidate": candidate,
            "engine": "one NautilusTrader BacktestNode",
            "account": "one shared USDT margin account",
            "symbols": list(PROJECT_SYMBOLS),
            "risk_fraction": 0.03,
            "global_constraint": "entry intents plus open positions <= 1",
            "economic_strategy": V7_WINNER,
            "economic_strategy_source": metrics["strategy_source"],
            "implementation_repair": (
                "compose original v52 class with existing strict shared slot and "
                "register importable per-symbol classes without overwriting v36"
            ),
            "original_workflow_failure": {
                "run_id": 31186753578,
                "failure": (
                    "workflow overwrote strategy_v36_cross_asset_repricing_gate.py "
                    "and removed SystemicRepricingGateMixin before runner import"
                ),
                "economic_backtest_reached": False,
            },
            "strategy_sequence": [
                "STRICTLY_PRIOR_COMPLETED_PEER_RETURNS",
                "OWN_MINUS_MEDIAN_PEER_5M_ATR_NORMALIZED_RESIDUAL",
                "ROBUST_MAD_EXTREME",
                "RESIDUAL_BEGINS_CONVERGING",
                "OWN_1M_INFLECTION_RELATIVE_TO_PEERS",
                "OPEN_INTEREST_NOT_EXPANDING",
                "TAIL_FLOW_AND_DEPTH_SUPPORT_CONVERGENCE",
                "INHERITED_CAUSAL_REJECTION_CONFIRMATION",
                "INHERITED_STRUCTURAL_STOP_AND_REAL_LIQUIDITY_TARGET",
            ],
            "same_timestamp_peer_information": "forbidden",
            "execution_and_nav": "existing shared NautilusTrader runner",
        },
    )
    return metrics


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--winner-evidence", type=Path, required=True)
    parser.add_argument("--build-start", required=True)
    parser.add_argument("--build-end", required=True)
    parser.add_argument("--evaluation-start", required=True)
    parser.add_argument("--evaluation-end", required=True)
    parser.add_argument("--cache", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    metrics = run_stage(args)
    print(json.dumps(metrics, indent=2, sort_keys=True, allow_nan=True))


if __name__ == "__main__":
    main()


__all__ = [
    "Candidate16V7BTCUSDTStrategy",
    "Candidate16V7ETHUSDTStrategy",
    "Candidate16V7SOLUSDTStrategy",
    "Candidate16V7XRPUSDTStrategy",
    "Candidate16V7SharedResidualStrategy",
    "PROJECT_SYMBOLS",
    "V7_WINNER",
    "candidate16_v7_strategy_path",
    "install_shared_adapter",
    "run_stage",
]
