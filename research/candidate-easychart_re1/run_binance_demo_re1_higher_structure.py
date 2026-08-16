#!/usr/bin/env python3
"""Run the frozen higher-structure candidate on Binance USD-M Demo Trading.

This launcher changes neither signal logic nor order lifecycle.  It injects the
same full scenario bundle used by the matched Nautilus backtest, preserves exact
Binance aggressor-flow fields through warmup and live bars, reconstructs H4 from
completed hourly candles, and uses the existing explicit market-entry plus
reduce-only protective lifecycle.

This is demo/paper only.  Unknown reconciled exposure is flattened and the
strategy remains halted until a clean restart.
"""
from __future__ import annotations

from datetime import UTC, datetime
import json
import sys

from easychart_re1_higher_structure_router import (
    COMPLETED_60M_STRUCTURE_RULE,
    LOCAL_STRUCTURE_ROUTER_RULE,
    EasyChartRE1HigherStructureRouterBundle,
)
from easychart_re1_skilled_continuation import (
    LOCAL_AUCTION_SKILLED_ROUTER_RULE,
)
from paper_re1_flow import (
    FLOW_PAPER_DATA_RULE,
    RESTART_RECONCILIATION_RULE,
    EasyChartRE1FlowCoherentPaperStrategy,
    build_flow_warmup_map,
)
from plan_event_values_re1 import plan_event_values
import run_binance_demo_re1 as base


CANDIDATE = "candidate-easychart_re1_completed_60m_structure_router"


class EasyChartRE1HigherStructurePaperStrategy(
    EasyChartRE1FlowCoherentPaperStrategy,
):
    """Paper runtime for the exact frozen higher-structure decision policy."""

    _plan_event_values = staticmethod(plan_event_values)


def _check_config() -> None:
    args = base.parse_args()
    symbols, instrument_ids = base._validated_inputs(args)
    bars = [base._bar_types(instrument_id) for instrument_id in instrument_ids]
    record = {
        "candidate": CANDIDATE,
        "environment": "BINANCE_DEMO_USDT_FUTURES",
        "scenario_bundle": EasyChartRE1HigherStructureRouterBundle.__name__,
        "paper_strategy": EasyChartRE1HigherStructurePaperStrategy.__name__,
        "symbols": symbols,
        "instrument_ids": [str(item) for item in instrument_ids],
        "execution_bar_types": [str(item[0]) for item in bars],
        "trigger_bar_types": [str(item[1]) for item in bars],
        "decision_bar_types": [str(item[2]) for item in bars],
        "higher_bar_types": [str(item[3]) for item in bars],
        "warmup_days": args.warmup_days,
        "flow_data_rule": FLOW_PAPER_DATA_RULE,
        "restart_reconciliation_rule": RESTART_RECONCILIATION_RULE,
        "higher_structure_rule": COMPLETED_60M_STRUCTURE_RULE,
        "local_router_rule": LOCAL_STRUCTURE_ROUTER_RULE,
        "auction_router_rule": LOCAL_AUCTION_SKILLED_ROUTER_RULE,
        "check_time_utc": datetime.now(UTC).isoformat(),
        "credentials_or_network_used": False,
    }
    print(json.dumps(record, ensure_ascii=False, indent=2, sort_keys=True))


def main() -> None:
    if "--check-config" in sys.argv:
        _check_config()
        return

    # ``base.main`` owns the battle-tested node, client, reconciliation and
    # protective-order construction.  Replace only the scenario, warmup and
    # strategy classes before it creates the TradingNode.
    base.EasyChartRE1FreshBundle = EasyChartRE1HigherStructureRouterBundle
    base.EasyChartRE1CoherentPaperStrategy = (
        EasyChartRE1HigherStructurePaperStrategy
    )
    base.build_warmup_map = build_flow_warmup_map
    base.main()


if __name__ == "__main__":
    main()
