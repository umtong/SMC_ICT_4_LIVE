#!/usr/bin/env python3
"""Run the integrated EasyChart policy with efficient-pullback continuation."""
from __future__ import annotations

import json
from pathlib import Path
import sys

from easychart_re1_efficient_pullback import (
    EFFICIENT_PULLBACK_IMPULSE_RULE,
    EFFICIENT_PULLBACK_LOCAL_LEG_RULE,
    EFFICIENT_PULLBACK_OBJECTIVE_RULE,
    EFFICIENT_PULLBACK_RESPONSE_RULE,
    EasyChartRE1EfficientPullbackBundle,
)
from easychart_re1_local_auction_continuation import EasyChartRE1LocalAuctionStrategy
import run_mtf_backtest_re1_flow as _flow_runner


_flow_runner._runner.EasyChartRE1NaturalBundle = EasyChartRE1EfficientPullbackBundle
_flow_runner._runner.EasyChartRE1Strategy = EasyChartRE1LocalAuctionStrategy


def _rewrite_metadata(output: Path) -> None:
    values = {
        "candidate": "candidate-easychart_re1_efficient_pullback",
        "policy": (
            "SPECIFIC_REJECTION_OB_FVG_HORIZONTAL_AND_DIAGONAL_OWNERS_PLUS_ACCEPTED_5M_FIRST_EFFICIENT_PULLBACK"
        ),
        "efficient_pullback_rules": [
            EFFICIENT_PULLBACK_LOCAL_LEG_RULE,
            EFFICIENT_PULLBACK_IMPULSE_RULE,
            EFFICIENT_PULLBACK_RESPONSE_RULE,
            EFFICIENT_PULLBACK_OBJECTIVE_RULE,
        ],
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
