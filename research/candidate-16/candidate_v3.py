#!/usr/bin/env python3
"""Candidate 16 v3 development entry point using the v2 NautilusTrader runner."""
from __future__ import annotations

import argparse
from datetime import date
import json
from pathlib import Path
from typing import Any

import candidate as candidate16_v2
import backtest as candidate05_backtest
from smc_ict_4.manifest import write_json_atomic
from strategy_v3 import Candidate16V3Strategy

_ORIGINAL_IMPORTABLE_STRATEGY_CONFIG = (
    candidate16_v2._ORIGINAL_IMPORTABLE_STRATEGY_CONFIG
)

# Evidence-only instrumentation. The inherited rejection behavior remains
# authoritative; this wrapper records the exact Nautilus event and portfolio
# state immediately before delegating to the unchanged handler.
_ORIGINAL_ON_ORDER_REJECTED = Candidate16V3Strategy.on_order_rejected


def _record_v3_order_rejection(self: Candidate16V3Strategy, event: Any) -> None:
    records = self.diagnostics.setdefault(
        "candidate16_v3_order_rejection_events",
        [],
    )
    assert isinstance(records, list)
    records.append(
        {
            "ts_event": int(getattr(event, "ts_event", 0)),
            "client_order_id": str(getattr(event, "client_order_id", "")),
            "current_scenario_id": self.current_scenario_id,
            "current_branch": self.current_branch,
            "portfolio_flat_before_handler": bool(
                self.portfolio.is_flat(self.config.instrument_id)
            ),
            "event": str(event),
        },
    )
    _ORIGINAL_ON_ORDER_REJECTED(self, event)


Candidate16V3Strategy.on_order_rejected = _record_v3_order_rejection


def _candidate16_v3_strategy_config(
    *,
    strategy_path: str,
    config_path: str,
    config: dict[str, Any],
):
    del strategy_path, config_path
    return _ORIGINAL_IMPORTABLE_STRATEGY_CONFIG(
        strategy_path="strategy_v3:Candidate16V3Strategy",
        config_path="strategy:Candidate16Config",
        config=config,
    )


candidate05_backtest.ImportableStrategyConfig = _candidate16_v3_strategy_config


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
    result["candidate"] = "candidate-16-v3-accepted-failure-next-source-delivery"
    result["reused_runner"] = "research/candidate-05/backtest.py"
    result["reused_state_router"] = "research/candidate-16/accepted_failure_router.py"
    result["strategy_path"] = "research/candidate-16/strategy_v3.py"
    result["evidence_instrumentation"] = "exact Nautilus order-rejection events"
    write_json_atomic(args.output.resolve() / "metrics.json", result)
    write_json_atomic(
        args.output.resolve() / "candidate16_contract.json",
        {
            "candidate": result["candidate"],
            "research_stage": "DEVELOPMENT",
            "engine": "NautilusTrader BacktestNode",
            "risk_fraction": 0.03,
            "max_global_entry_or_position": 1,
            "unchanged_state_policy": [
                "BOUNDARY_BREACH",
                "TWO_COMPLETED_OUTSIDE_CLOSE_ACCEPTANCE",
                "LATER_ACCEPTED_BOUNDARY_FAILURE",
                "LATER_INDEPENDENT_FAILURE_LEG_TRIGGER",
            ],
            "single_changed_role": "delivery_objective",
            "target_policy": [
                "failed source range opposite edge",
                "next live completed source-auction boundary known when breach began",
            ],
            "forbidden_fallbacks": [
                "synthetic R multiple",
                "MFE-derived target",
                "fixed percentage target",
                "reduced min_target_net_r",
                "future-created source level",
            ],
            "invalidation": "unchanged failed boundary plus failure/trigger extremes",
            "evidence_only_instrumentation": "record rejection event then delegate unchanged handler",
            "runner_snapshot": "candidate-05@e9c858247ef5247bc3f4d8ad3f0de078a7ecebb0",
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
