#!/usr/bin/env python3
"""Run the integrated policy with corrected macro-pivot lifecycle."""
from __future__ import annotations

import json
from pathlib import Path
import sys

from easychart_re1_auction_router_v5 import (
    FAILED_MACRO_BREAK_REUSABLE_PIVOT_RULE,
    EasyChartRE1AuctionRouterV5Bundle,
)
from easychart_re1_local_auction_continuation import EasyChartRE1LocalAuctionStrategy
import run_mtf_backtest_re1_flow as _flow_runner


_flow_runner._runner.EasyChartRE1NaturalBundle = EasyChartRE1AuctionRouterV5Bundle
_flow_runner._runner.EasyChartRE1Strategy = EasyChartRE1LocalAuctionStrategy


def _rewrite_metadata(output: Path) -> None:
    values = {
        "candidate": "candidate-easychart_re1_auction_router_v5",
        "policy": (
            "SPECIFIC_AUCTION_OWNERS_WITH_ACCEPTANCE_CONFIRMED_AND_REUSABLE_FAILED_60M_BREAK_PIVOTS"
        ),
        "macro_pivot_lifecycle_rule": FAILED_MACRO_BREAK_REUSABLE_PIVOT_RULE,
    }
    for name in ("metrics.json", "run.json"):
        path = output / name
        if not path.exists():
            continue
        record = json.loads(path.read_text(encoding="utf-8"))
        record.update(values)
        path.write_text(
            json.dumps(record, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )


if __name__ == "__main__":
    destination = _flow_runner._output_path(sys.argv)
    _flow_runner._runner.main()
    if destination is not None:
        _flow_runner._rewrite_metadata(destination)
        _rewrite_metadata(destination)
