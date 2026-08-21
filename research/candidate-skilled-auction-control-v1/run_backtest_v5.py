#!/usr/bin/env python3
"""Execute structural auction control v5 in the existing continuous account."""
from __future__ import annotations

import json
from pathlib import Path
import sys

import run_mtf_backtest_re1_flow as _flow
from easychart_re1_local_auction_continuation import EasyChartRE1LocalAuctionStrategy
from structural_auction_control_v5 import (
    ACCEPTED_AUCTION_JOURNEY_RULE,
    DEFENDED_AUCTION_JOURNEY_RULE,
    EVENT_TIME_JOURNEY_RULE,
    FAILED_AUCTION_JOURNEY_RULE,
    FIRST_CAUSAL_DESTINATION_RULE,
    ONE_CAUSAL_JOURNEY_RULE,
)
from structural_auction_control_v5_market import StructuralAuctionControlV5MarketBundle


# Retain the exact aggressor-flow data, order lifecycle, costs, 3% quantity and
# single global account.  The market-factor strategy observes the completed
# four-symbol one-minute bucket, then the integration bundle propagates that
# state to every compatible structural sensor before local decisions are made.
_flow._runner.EasyChartRE1NaturalBundle = StructuralAuctionControlV5MarketBundle
_flow._runner.EasyChartRE1Strategy = EasyChartRE1LocalAuctionStrategy


def _rewrite(output: Path) -> None:
    _flow._rewrite_metadata(output)
    metadata = {
        "candidate": "candidate-structural-auction-control-v5-market",
        "decision_policy": (
            "event-time public auction journey: failed auction, accepted auction "
            "or defended auction -> immutable structural invalidation and first causal destination"
        ),
        "market_context_policy": (
            "completed BTC/ETH-led three-of-four common initiative is propagated before "
            "local decisions and may veto an opposing local first touch"
        ),
        "mechanism_owners": [
            "FAILED_AUCTION_REVERSAL",
            "ACCEPTED_AUCTION_CONTINUATION",
            "HORIZONTAL_ACCEPTANCE",
            "STRUCTURAL_PULLBACK",
            "DEFENDED_AUCTION_CONTINUATION",
        ],
        "rule_provenance": [
            EVENT_TIME_JOURNEY_RULE,
            FAILED_AUCTION_JOURNEY_RULE,
            ACCEPTED_AUCTION_JOURNEY_RULE,
            DEFENDED_AUCTION_JOURNEY_RULE,
            FIRST_CAUSAL_DESTINATION_RULE,
            ONE_CAUSAL_JOURNEY_RULE,
        ],
    }
    for name in ("metrics.json", "run.json"):
        path = output / name
        if not path.exists():
            continue
        record = json.loads(path.read_text(encoding="utf-8"))
        record.update(metadata)
        path.write_text(
            json.dumps(record, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )


if __name__ == "__main__":
    destination = _flow._output_path(sys.argv)
    _flow._runner.main()
    if destination is not None:
        _rewrite(destination)
