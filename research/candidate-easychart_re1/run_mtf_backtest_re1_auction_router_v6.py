#!/usr/bin/env python3
"""Run the integrated policy with persistent common-flow veto only."""
from __future__ import annotations

import json
from pathlib import Path
import sys

from easychart_re1_auction_router_v6 import (
    PERSISTENT_COMMON_VETO_ONLY_RULE,
    EasyChartRE1AuctionRouterV6Bundle,
    EasyChartRE1PersistentVetoStrategy,
)
import run_mtf_backtest_re1_flow as _flow_runner


_flow_runner._runner.EasyChartRE1NaturalBundle = EasyChartRE1AuctionRouterV6Bundle
_flow_runner._runner.EasyChartRE1Strategy = EasyChartRE1PersistentVetoStrategy


def _rewrite_metadata(output: Path) -> None:
    values = {
        "candidate": "candidate-easychart_re1_auction_router_v6",
        "policy": (
            "V5_SPECIFIC_AUCTIONS_WITH_INSTANTANEOUS_OR_PERSISTENT_OPPOSING_COMMON_FLOW_VETO_ONLY"
        ),
        "persistent_common_veto_rule": PERSISTENT_COMMON_VETO_ONLY_RULE,
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
