#!/usr/bin/env python3
"""Run the displacement-confirmed core on Binance USD-M Demo Trading.

The paper launcher injects the exact scenario bundle used by the NautilusTrader
backtest.  Historical catch-up and live bars preserve Binance quote volume,
trade count and taker-buy fields; completed 60m/15m/5m state is routed before
one-minute decisions; the existing explicit market-entry plus independent
reduce-only stop/target lifecycle is reused unchanged.

This is demo/paper only.  Unknown reconciled exposure is canceled and flattened,
then the strategy remains halted until a clean restart.  Every decision and
execution event is appended to durable JSONL and critical lifecycle state is
atomically snapshotted for restart diagnosis.
"""
from __future__ import annotations

from datetime import UTC, datetime
import json
import os
from pathlib import Path
import sys

from easychart_re1_displacement_confirmed_auction import (
    BODY_DOMINANT_DELIVERY_RULE,
    DISPLACEMENT_CONFIRMED_AUCTION_RULE,
    WEAK_LOCAL_FAMILY_RETIREMENT_RULE,
    EasyChartRE1DisplacementConfirmedAuctionBundle,
)
from paper_re1_durable_audit import (
    PAPER_EVENT_LOG_ENV,
    PAPER_STATE_SNAPSHOT_ENV,
    DurablePaperAuditMixin,
)
from paper_re1_flow import (
    FLOW_PAPER_DATA_RULE,
    RESTART_RECONCILIATION_RULE,
    EasyChartRE1FlowCoherentPaperStrategy,
    build_flow_warmup_map,
)
from plan_event_values_re1 import plan_event_values
import run_binance_demo_re1 as base


CANDIDATE = "candidate-easychart_re1_displacement_confirmed_core"
DEFAULT_EVENT_LOG = Path(f".state/{CANDIDATE}/paper_events.jsonl")
DEFAULT_STATE_SNAPSHOT = Path(f".state/{CANDIDATE}/paper_state.json")


class EasyChartRE1DisplacementCorePaperStrategy(
    DurablePaperAuditMixin,
    EasyChartRE1FlowCoherentPaperStrategy,
):
    """Flow-preserving fail-closed paper runtime for the frozen core."""

    _plan_event_values = staticmethod(plan_event_values)


def _check_config() -> None:
    args = base.parse_args()
    symbols, instrument_ids = base._validated_inputs(args)
    bars = [base._bar_types(instrument_id) for instrument_id in instrument_ids]
    record = {
        "candidate": CANDIDATE,
        "environment": "BINANCE_DEMO_USDT_FUTURES",
        "scenario_bundle": EasyChartRE1DisplacementConfirmedAuctionBundle.__name__,
        "paper_strategy": EasyChartRE1DisplacementCorePaperStrategy.__name__,
        "symbols": symbols,
        "instrument_ids": [str(item) for item in instrument_ids],
        "execution_bar_types": [str(item[0]) for item in bars],
        "trigger_bar_types": [str(item[1]) for item in bars],
        "decision_bar_types": [str(item[2]) for item in bars],
        "higher_bar_types": [str(item[3]) for item in bars],
        "warmup_days": args.warmup_days,
        "flow_data_rule": FLOW_PAPER_DATA_RULE,
        "restart_reconciliation_rule": RESTART_RECONCILIATION_RULE,
        "body_dominant_delivery_rule": BODY_DOMINANT_DELIVERY_RULE,
        "displacement_confirmed_auction_rule": DISPLACEMENT_CONFIRMED_AUCTION_RULE,
        "weak_local_family_retirement_rule": WEAK_LOCAL_FAMILY_RETIREMENT_RULE,
        "retired_local_scales": sorted(
            EasyChartRE1DisplacementConfirmedAuctionBundle.RETIRED_LOCAL_SCALES,
        ),
        "durable_event_log": os.environ.get(
            PAPER_EVENT_LOG_ENV,
            str(DEFAULT_EVENT_LOG),
        ),
        "atomic_state_snapshot": os.environ.get(
            PAPER_STATE_SNAPSHOT_ENV,
            str(DEFAULT_STATE_SNAPSHOT),
        ),
        "check_time_utc": datetime.now(UTC).isoformat(),
        "credentials_or_network_used": False,
    }
    print(json.dumps(record, ensure_ascii=False, indent=2, sort_keys=True))


def main() -> None:
    if "--check-config" in sys.argv:
        _check_config()
        return

    os.environ.setdefault(PAPER_EVENT_LOG_ENV, str(DEFAULT_EVENT_LOG))
    os.environ.setdefault(
        PAPER_STATE_SNAPSHOT_ENV,
        str(DEFAULT_STATE_SNAPSHOT),
    )

    # The base launcher owns the proven TradingNode/client/reconciliation and
    # protective-order lifecycle.  Replace only the scenario, warmup and paper
    # strategy classes before node construction.
    base.EasyChartRE1FreshBundle = EasyChartRE1DisplacementConfirmedAuctionBundle
    base.EasyChartRE1CoherentPaperStrategy = (
        EasyChartRE1DisplacementCorePaperStrategy
    )
    base.build_warmup_map = build_flow_warmup_map
    base.main()


if __name__ == "__main__":
    main()
