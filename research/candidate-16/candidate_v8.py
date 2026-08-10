#!/usr/bin/env python3
"""Candidate 16 v8 shared-account adapter and runner.

The existing Candidate 05 one-account four-symbol runner, per-symbol data
preparation, cost model, margin account, NautilusTrader engine and audited final
global slot are reused unchanged. Candidate 16 v8 changes only the economic
state transition after the original v52 detector freezes a residual state.

This adapter also enforces mechanism purity for the diagnostic account. Candidate
05 v52 inherits a mature strategy stack which contains an independent
position-building balance-acceptance family. That family is useful elsewhere,
but a profit or loss from it cannot test the residual-convergence hypothesis.
The balance observer is therefore disabled in this isolated experiment and any
other inherited entry submission is treated as a fatal implementation error.
"""
from __future__ import annotations

import argparse
from datetime import date
import importlib.util
import json
from pathlib import Path
import sys
from typing import Any

from global_entry_slot_v4 import FINAL_SHARED_ACCOUNT_ENTRY_COORDINATOR
import strategy_global_slot_wrappers_v4 as shared_v4
import strategy_global_slot_wrappers_v5 as shared_v5  # noqa: F401


def _load_candidate16_v8_strategy_class():
    """Load this directory's strategy_v8.py without relying on PYTHONPATH order.

    Candidate 05 also contains a module named ``strategy_v8``. The shared
    runner intentionally keeps Candidate 05 first on PYTHONPATH because v8
    reuses its account/execution stack. A normal ``from strategy_v8 import``
    therefore resolves to the wrong economic module. Loading the sibling file
    under a unique module name fixes only that namespace collision and leaves
    all Candidate 05 dependencies used by the strategy unchanged.
    """
    module_name = "_candidate16_strategy_v8"
    module = sys.modules.get(module_name)
    if module is None:
        source = Path(__file__).resolve().with_name("strategy_v8.py")
        spec = importlib.util.spec_from_file_location(module_name, source)
        if spec is None or spec.loader is None:
            raise ImportError(f"unable to load Candidate 16 v8 strategy from {source}")
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        spec.loader.exec_module(module)
    strategy_class = getattr(module, "Candidate16V8Strategy", None)
    if strategy_class is None:
        raise ImportError("Candidate16V8Strategy missing from Candidate 16 strategy_v8.py")
    return strategy_class


Candidate16V8Strategy = _load_candidate16_v8_strategy_class()
_STRATEGY_V8_MODULE = sys.modules[Candidate16V8Strategy.__module__]
V8_TRADE_BRANCH = str(getattr(_STRATEGY_V8_MODULE, "V8_TRADE_BRANCH"))


V8_WINNER = "candidate_v8:Candidate16V8Strategy"
PROJECT_SYMBOLS = ("BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT")


if shared_v4.SHARED_ACCOUNT_ENTRY_COORDINATOR is not FINAL_SHARED_ACCOUNT_ENTRY_COORDINATOR:
    raise RuntimeError("final shared-account coordinator was not installed")


class Candidate16V8SharedStrategy(
    shared_v4.SharedAccountEntryLifecycleMixin,
    Candidate16V8Strategy,
):
    """Residual-only v8 economics with the audited one-global-slot lifecycle."""

    def __init__(self, config: Any) -> None:
        super().__init__(config)
        self.diagnostics.update(
            {
                "candidate16_v8_legacy_balance_observer_calls_suppressed": 0,
                "candidate16_v8_non_residual_submission_attempts": 0,
            },
        )

    def _detect_position_building_balance(
        self,
        row: dict[str, float | int],
    ) -> None:
        """Keep the independent PBA family out of the residual diagnosis.

        v52 inherits this detector through Candidate 05's v16-v26 stack. A PBA
        fill is economically real but says nothing about residual convergence,
        so allowing it would make the experiment mechanism-contaminated.
        """
        del row
        self.diagnostics[
            "candidate16_v8_legacy_balance_observer_calls_suppressed"
        ] = int(
            self.diagnostics[
                "candidate16_v8_legacy_balance_observer_calls_suppressed"
            ],
        ) + 1

    def _submit_price_capped_bracket(self, *args: Any, **kwargs: Any) -> bool:
        """Permit only the v8 residual branch in this diagnostic account."""
        branch = str(kwargs.get("branch", ""))
        if branch != V8_TRADE_BRANCH:
            self.diagnostics["candidate16_v8_non_residual_submission_attempts"] = int(
                self.diagnostics["candidate16_v8_non_residual_submission_attempts"],
            ) + 1
            raise RuntimeError(
                "NON_RESIDUAL_ENTRY_PATH_ATTEMPTED_IN_V8_ISOLATED_ACCOUNT: "
                f"{branch or '<missing>'}",
            )
        return super()._submit_price_capped_bracket(*args, **kwargs)


class Candidate16V8BTCUSDTStrategy(Candidate16V8SharedStrategy):
    pass


class Candidate16V8ETHUSDTStrategy(Candidate16V8SharedStrategy):
    pass


class Candidate16V8SOLUSDTStrategy(Candidate16V8SharedStrategy):
    pass


class Candidate16V8XRPUSDTStrategy(Candidate16V8SharedStrategy):
    pass


def candidate16_v8_strategy_path(winner: str, symbol: str) -> str:
    if winner != V8_WINNER:
        raise ValueError(f"unsupported Candidate 16 v8 winner: {winner}")
    if symbol not in PROJECT_SYMBOLS:
        raise ValueError(f"unsupported project symbol: {symbol}")
    return f"candidate_v8:Candidate16V8{symbol}Strategy"


def install_shared_adapter():
    import shared_account_strategy_variants_v2 as variants
    import shared_account_backtest_v2 as runner

    variants.WINNER_TO_FAMILY[V8_WINNER] = "v52"
    variants.final_shared_strategy_path = candidate16_v8_strategy_path
    runner._base.final_shared_strategy_path = candidate16_v8_strategy_path
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
    candidate = "candidate-16-v8-later-residual-convergence"
    metrics["candidate"] = candidate
    metrics["validation_mode"] = "pre_registered_one_account_four_symbol_screen"
    metrics["state_source"] = (
        "research/candidate-05/strategy_v52_cross_sectional_residual.py"
    )
    metrics["transition_source"] = "research/candidate-16/strategy_v8.py"
    metrics["mechanism_purity"] = "V8_LATER_RESIDUAL_CONVERGENCE_ONLY"
    write_json(args.output.resolve() / "metrics.json", metrics)

    run_path = args.output.resolve() / "run.json"
    run_payload = json.loads(run_path.read_text(encoding="utf-8"))
    run_payload["candidate"] = candidate
    run_payload["strategy"] = V8_WINNER
    run_payload["candidate16_v8"] = {
        "state_detector_changed": False,
        "state_source": metrics["state_source"],
        "transition_source": metrics["transition_source"],
        "same_bar_confirmation_removed": True,
        "strictly_later_convergence_required": True,
        "entry_time_in_force": "FOK",
        "minimum_natural_target_net_r": 1.0,
        "mechanism_purity": "V8_LATER_RESIDUAL_CONVERGENCE_ONLY",
        "inherited_position_building_balance_observer": "DISABLED",
        "any_other_inherited_entry_submission": "FATAL_IMPLEMENTATION_ERROR",
    }
    write_json(run_path, run_payload)

    write_json(
        args.output.resolve() / "candidate16_v8_contract.json",
        {
            "candidate": candidate,
            "engine": "one NautilusTrader BacktestNode",
            "account": "one shared USDT margin account",
            "symbols": list(PROJECT_SYMBOLS),
            "risk_fraction": 0.03,
            "global_constraint": "entry intents plus open positions <= 1",
            "mechanism_purity": "V8_LATER_RESIDUAL_CONVERGENCE_ONLY",
            "state_detector": (
                "unchanged Candidate 05 v52 robust residual, OI, tail-flow and depth"
            ),
            "state_source": metrics["state_source"],
            "transition_source": metrics["transition_source"],
            "sequence": [
                "STRICTLY_PRIOR_COMPLETED_PEER_RETURNS",
                "ROBUST_FOUR_ASSET_RESIDUAL_STATE",
                "OI_NOT_EXPANDING_AND_LOCAL_TAIL_FLOW_DEPTH_STATE",
                "STATE_FROZEN_NO_ORDER",
                "STRICTLY_LATER_RESIDUAL_CONTRACTION",
                "STRICTLY_LATER_PRICE_AND_RELATIVE_RETURN_CONVERGENCE",
                "STRICTLY_LATER_FLOW_AND_DEPTH_CONVERGENCE",
                "FOK_PRICE_CAPPED_ENTRY",
                "STATE_TO_CONFIRMATION_EXTREME_INVALIDATION",
                "PRE_EXISTING_LIQUIDITY_OBJECTIVE_AT_LEAST_ONE_NET_R",
            ],
            "same_timestamp_peer_information": "forbidden",
            "same_bar_state_confirmation_reuse": "forbidden",
            "inherited_position_building_balance_observer": "disabled",
            "other_inherited_entry_branches": "fatal if reached",
            "state_wait_horizon_minutes": 15,
            "target_fallback": "forbidden",
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
    "Candidate16V8BTCUSDTStrategy",
    "Candidate16V8ETHUSDTStrategy",
    "Candidate16V8SOLUSDTStrategy",
    "Candidate16V8XRPUSDTStrategy",
    "Candidate16V8SharedStrategy",
    "PROJECT_SYMBOLS",
    "V8_TRADE_BRANCH",
    "V8_WINNER",
    "candidate16_v8_strategy_path",
    "install_shared_adapter",
    "run_stage",
]
