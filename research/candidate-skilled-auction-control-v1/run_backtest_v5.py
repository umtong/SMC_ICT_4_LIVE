#!/usr/bin/env python3
"""Execute structural auction control v5 in the existing continuous account."""
from __future__ import annotations

import json
from pathlib import Path
import sys

import run_mtf_backtest_re1_flow as _flow
from structural_auction_control_v5 import (
    ACCEPTED_AUCTION_JOURNEY_RULE,
    DEFENDED_AUCTION_JOURNEY_RULE,
    EVENT_TIME_JOURNEY_RULE,
    FAILED_AUCTION_JOURNEY_RULE,
    FIRST_CAUSAL_DESTINATION_RULE,
    ONE_CAUSAL_JOURNEY_RULE,
    StructuralAuctionControlV5Bundle,
)


# Keep the existing aggressor-flow market data, Nautilus strategy, fills, costs,
# position sizing and global account arbitration. Replace only the policy bundle.
_flow._runner.EasyChartRE1NaturalBundle = StructuralAuctionControlV5Bundle


def _rewrite(output: Path) -> None:
    _flow._rewrite_metadata(output)
    metadata = {
        "candidate": "candidate-structural-auction-control-v5",
        "decision_policy": (
            "event-time public auction journey: failed auction, accepted auction "
            "or defended auction -> immutable structural invalidation and first causal destination"
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
