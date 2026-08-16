#!/usr/bin/env python3
"""Run the integrated EasyChart policy with frozen-target efficient pullbacks."""
from __future__ import annotations

import json
from pathlib import Path
import sys

from easychart_re1_efficient_pullback import (
    EFFICIENT_PULLBACK_IMPULSE_RULE,
    EFFICIENT_PULLBACK_LOCAL_LEG_RULE,
    EFFICIENT_PULLBACK_OBJECTIVE_RULE,
    EFFICIENT_PULLBACK_RESPONSE_RULE,
)
from easychart_re1_efficient_pullback_v2 import (
    EFFICIENT_PULLBACK_FROZEN_TARGET_RULE,
    EasyChartRE1EfficientPullbackV2Bundle,
)
from easychart_re1_local_auction_continuation import EasyChartRE1LocalAuctionStrategy
import run_mtf_backtest_re1_flow as _flow_runner


_flow_runner._runner.EasyChartRE1NaturalBundle = EasyChartRE1EfficientPullbackV2Bundle
_flow_runner._runner.EasyChartRE1Strategy = EasyChartRE1LocalAuctionStrategy


def _rewrite_metadata(output: Path) -> None:
    values = {
        "candidate": "candidate-easychart_re1_efficient_pullback_v2",
        "policy": (
            "SPECIFIC_EXISTING_AUCTIONS_PLUS_ACCEPTED_5M_FIRST_EFFICIENT_PULLBACK_WITH_TARGET_FROZEN_ON_HOLD"
        ),
        "efficient_pullback_rules": [
            EFFICIENT_PULLBACK_LOCAL_LEG_RULE,
            EFFICIENT_PULLBACK_IMPULSE_RULE,
            EFFICIENT_PULLBACK_RESPONSE_RULE,
            EFFICIENT_PULLBACK_OBJECTIVE_RULE,
            EFFICIENT_PULLBACK_FROZEN_TARGET_RULE,
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
