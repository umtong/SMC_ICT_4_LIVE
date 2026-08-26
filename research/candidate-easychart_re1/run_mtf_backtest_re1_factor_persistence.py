#!/usr/bin/env python3
"""Run the persistent common-auction router on the quality RE1 core."""
from __future__ import annotations

import json
from pathlib import Path
import sys

from easychart_re1_channel_abstention import EasyChartRE1ChannelAbstentionBundle
from execution_re1_factor_persistence import (
    PERSISTENT_ALIGNED_ROUTING_RULE,
    PERSISTENT_COMMON_AUCTION_RULE,
    TRANSITIONAL_VISUAL_OWNERSHIP_RULE,
    TURBULENT_ABSTENTION_RULE,
    EasyChartRE1PersistentFactorStrategy,
)
import run_mtf_backtest_re1_flow as _flow_runner

_flow_runner._runner.EasyChartRE1NaturalBundle = EasyChartRE1ChannelAbstentionBundle
_flow_runner._runner.EasyChartRE1Strategy = EasyChartRE1PersistentFactorStrategy


def _rewrite_metadata(output: Path) -> None:
    values = {
        "candidate": "candidate-easychart_re1_factor_persistence",
        "decision_policy": (
            "latest six common BTC/ETH/SOL/XRP initiative events route persistent aligned "
            "pullbacks, turbulent states abstain, and transitional states retain only visual "
            "OB/FVG or major-liquidity episodes"
        ),
        "persistent_factor_rules": [
            PERSISTENT_COMMON_AUCTION_RULE,
            PERSISTENT_ALIGNED_ROUTING_RULE,
            TURBULENT_ABSTENTION_RULE,
            TRANSITIONAL_VISUAL_OWNERSHIP_RULE,
        ],
    }
    for name in ("metrics.json", "run.json"):
        path = output / name
        if not path.exists():
            continue
        record = json.loads(path.read_text(encoding="utf-8"))
        record.update(values)
        path.write_text(json.dumps(record, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


if __name__ == "__main__":
    destination = _flow_runner._output_path(sys.argv)
    _flow_runner._runner.main()
    if destination is not None:
        _flow_runner._rewrite_metadata(destination)
        _rewrite_metadata(destination)
