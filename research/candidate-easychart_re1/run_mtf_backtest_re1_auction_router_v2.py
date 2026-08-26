#!/usr/bin/env python3
"""Run the integrated router with raw horizontal flip ownership."""
from __future__ import annotations

import json
from pathlib import Path
import sys

from easychart_re1_auction_router import MECHANISM_SPECIFIC_CONTEXT_ROUTER_RULE
from easychart_re1_auction_router_fvg import LOCAL_FVG_CONTINUATION_RULE
from easychart_re1_auction_router_v2 import (
    DIRECT_HORIZONTAL_FLIP_ENGINE_RULE,
    EasyChartRE1AuctionRouterV2Bundle,
)
from easychart_re1_local_auction_continuation import EasyChartRE1LocalAuctionStrategy
import run_mtf_backtest_re1_flow as _flow_runner


_flow_runner._runner.EasyChartRE1NaturalBundle = EasyChartRE1AuctionRouterV2Bundle
_flow_runner._runner.EasyChartRE1Strategy = EasyChartRE1LocalAuctionStrategy


def _rewrite_metadata(output: Path) -> None:
    values = {
        "candidate": "candidate-easychart_re1_auction_router_v2",
        "policy": (
            "MECHANISM_ROUTED_REJECTION_OR_LOCAL_OB_FVG_CONTINUATION_OR_RAW_HORIZONTAL_FLIP_RESPONSE"
        ),
        "mechanism_router_rule": MECHANISM_SPECIFIC_CONTEXT_ROUTER_RULE,
        "local_fvg_rule": LOCAL_FVG_CONTINUATION_RULE,
        "direct_horizontal_flip_rule": DIRECT_HORIZONTAL_FLIP_ENGINE_RULE,
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
