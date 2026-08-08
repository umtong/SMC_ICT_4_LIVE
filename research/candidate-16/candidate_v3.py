#!/usr/bin/env python3
"""Candidate 16 v3 development entry point using the v2 NautilusTrader runner."""
from __future__ import annotations

import argparse
from datetime import date, datetime, timezone
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
_ORIGINAL_ON_POSITION_CLOSED = Candidate16V3Strategy.on_position_closed


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


def _serialized_v3_forced_exit(
    self: Candidate16V3Strategy,
    row: dict[str, float | int],
) -> None:
    """Cancel protection, observe cancellation, then close if still non-flat.

    The inherited callback submitted cancel and reduce-only market-close
    commands together. A protective child could fill between those commands,
    leaving a stale reduce-only close which Nautilus correctly rejected. This
    execution-only repair serializes the same forced-exit intent across bars.
    """
    moment = datetime.fromtimestamp(
        int(row["ts"]) / 1_000_000_000,
        tz=timezone.utc,
    )
    before_funding = (
        moment.hour in (7, 15, 23)
        and moment.minute >= self.config.funding_flatten_minute
    )
    timed_out = (
        self.position_open_index >= 0
        and self.bar_index - self.position_open_index >= self.config.max_hold_bars
    )
    evaluation_ended = int(row["ts"]) >= self.config.evaluation_end_ns
    if not (before_funding or timed_out or evaluation_ended):
        return

    phase = getattr(self, "_v3_forced_exit_phase", None)
    if phase is None:
        self.cancel_all_orders(self.config.instrument_id)
        self._v3_forced_exit_phase = "CANCEL_REQUESTED"
        self.diagnostics["candidate16_v3_serialized_exit_requests"] = int(
            self.diagnostics.get("candidate16_v3_serialized_exit_requests", 0)
        ) + 1
        if self.current_scenario_id is not None:
            self._transition(
                self.current_scenario_id,
                "FORCED_DAYTRADE_EXIT",
                int(row["ts"]),
                int(row["ts"]),
                "EXIT_PENDING",
                "FUNDING_OR_HOLD_OR_EVALUATION_BOUNDARY",
                float(row["close"]),
                {
                    "before_funding": before_funding,
                    "timed_out": timed_out,
                    "evaluation_ended": evaluation_ended,
                    "execution_phase": "CANCEL_REQUESTED",
                },
            )
        return

    if self.portfolio.is_flat(self.config.instrument_id):
        self._v3_forced_exit_phase = None
        return
    if phase == "CLOSE_SUBMITTED":
        return

    active_orders = (
        int(self.cache.orders_open_count(instrument_id=self.config.instrument_id))
        + int(self.cache.orders_inflight_count(instrument_id=self.config.instrument_id))
    )
    if active_orders:
        return

    self.close_all_positions(self.config.instrument_id)
    self._v3_forced_exit_phase = "CLOSE_SUBMITTED"
    self.diagnostics["candidate16_v3_serialized_market_closes"] = int(
        self.diagnostics.get("candidate16_v3_serialized_market_closes", 0)
    ) + 1


def _reset_v3_exit_phase(self: Candidate16V3Strategy, event: Any) -> None:
    _ORIGINAL_ON_POSITION_CLOSED(self, event)
    self._v3_forced_exit_phase = None


Candidate16V3Strategy.on_order_rejected = _record_v3_order_rejection
Candidate16V3Strategy._manage_open_position = _serialized_v3_forced_exit
Candidate16V3Strategy.on_position_closed = _reset_v3_exit_phase


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
    result["execution_repair"] = "serialized cancel-observe-close forced exit"
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
            "execution_only_repair": "cancel protection, wait for zero open/inflight orders, then close only if non-flat",
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
