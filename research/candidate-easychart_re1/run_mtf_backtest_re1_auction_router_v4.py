#!/usr/bin/env python3
"""Run the specifically-owned integrated EasyChart auction policy."""
from __future__ import annotations

import json
from pathlib import Path
import sys

from easychart_re1_auction_router_v4 import (
    SPECIFIC_ENTRY_OWNER_PRIORITY_RULE,
    EasyChartRE1AuctionRouterV4Bundle,
)
from easychart_re1_local_auction_continuation import EasyChartRE1LocalAuctionStrategy
import run_mtf_backtest_re1_flow as _flow_runner


_flow_runner._runner.EasyChartRE1NaturalBundle = EasyChartRE1AuctionRouterV4Bundle
_flow_runner._runner.EasyChartRE1Strategy = EasyChartRE1LocalAuctionStrategy


def _rewrite_metadata(output: Path) -> None:
    values = {
        "candidate": "candidate-easychart_re1_auction_router_v4",
        "policy": (
            "SPECIFIC_REJECTION_OB_FVG_HORIZONTAL_OWNERS_BEFORE_RESIDUAL_MATURE_DIAGONAL_ACCEPTANCE"
        ),
        "entry_owner_rule": SPECIFIC_ENTRY_OWNER_PRIORITY_RULE,
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
