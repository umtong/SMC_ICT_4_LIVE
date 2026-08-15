#!/usr/bin/env python3
"""Run persistent common-auction routing plus five-minute footprint continuation."""
from __future__ import annotations

import json
from pathlib import Path
import sys

from easychart_re1_channel_abstention import CHANNEL_REVERSAL_ABSTENTION_RULE
from easychart_re1_persistent_continuation import (
    PERSISTENT_CONTINUATION_FORMATION_RULE,
    PERSISTENT_FIRST_RETURN_RULE,
    PERSISTENT_REBALANCE_RULE,
    EasyChartRE1PersistentContinuationBundle,
    PersistentContinuationMarketStrategy,
)
from execution_re1_factor_persistence import (
    PERSISTENT_ALIGNED_ROUTING_RULE,
    PERSISTENT_COMMON_AUCTION_RULE,
    TRANSITIONAL_VISUAL_OWNERSHIP_RULE,
    TURBULENT_ABSTENTION_RULE,
)
import run_mtf_backtest_re1_flow as _flow_runner

_flow_runner._runner.EasyChartRE1NaturalBundle = EasyChartRE1PersistentContinuationBundle
_flow_runner._runner.EasyChartRE1Strategy = PersistentContinuationMarketStrategy


def _rewrite_metadata(output: Path) -> None:
    values = {
        "candidate": "candidate-easychart_re1_persistent_continuation",
        "decision_policy": (
            "persistent common BTC/ETH/SOL/XRP initiative routes aligned participating-symbol "
            "plans and creates flow-validated five-minute OB/FVG pullback continuations; turbulent "
            "states abstain and transitional states retain only visual or major-liquidity reversals"
        ),
        "channel_abstention_rule": CHANNEL_REVERSAL_ABSTENTION_RULE,
        "persistent_factor_rules": [
            PERSISTENT_COMMON_AUCTION_RULE,
            PERSISTENT_ALIGNED_ROUTING_RULE,
            TURBULENT_ABSTENTION_RULE,
            TRANSITIONAL_VISUAL_OWNERSHIP_RULE,
        ],
        "persistent_continuation_rules": [
            PERSISTENT_CONTINUATION_FORMATION_RULE,
            PERSISTENT_REBALANCE_RULE,
            PERSISTENT_FIRST_RETURN_RULE,
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
