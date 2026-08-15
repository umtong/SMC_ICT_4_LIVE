#!/usr/bin/env python3
"""Run all regime-specific RE1 auctions under the 15-minute common-flow state."""
from __future__ import annotations

import json
from pathlib import Path
import sys

from easychart_re1_persistent_continuation import (
    PERSISTENT_CONTINUATION_FORMATION_RULE,
    PERSISTENT_FIRST_RETURN_RULE,
    PERSISTENT_REBALANCE_RULE,
)
from easychart_re1_turbulent_contraction import (
    CONTRACTION_OBJECTIVE_RULE,
    TURBULENT_ADVERSE_FLOW_RULE,
    TURBULENT_CONTRACTION_RULE,
    EasyChartRE1FullAuctionBundle,
)
from execution_re1_factor_persistence import (
    PERSISTENT_ALIGNED_ROUTING_RULE,
    PERSISTENT_COMMON_AUCTION_RULE,
    TRANSITIONAL_VISUAL_OWNERSHIP_RULE,
    TURBULENT_ABSTENTION_RULE,
)
from execution_re1_slow_common import (
    SLOW_COMMON_AUCTION_RULE,
    SLOW_COMMON_HOLD_RULE,
    SLOW_COMMON_MATERIALITY_RULE,
    SlowFullAuctionStateStrategy,
)
import run_mtf_backtest_re1_flow as _flow_runner

_flow_runner._runner.EasyChartRE1NaturalBundle = EasyChartRE1FullAuctionBundle
_flow_runner._runner.EasyChartRE1Strategy = SlowFullAuctionStateStrategy


def _rewrite_metadata(output: Path) -> None:
    values = {
        "candidate": "candidate-easychart_re1_slow_full_auction",
        "decision_policy": (
            "15m common-flow persistence owns flow-validated 5m footprint continuation; "
            "15m common-flow turbulence owns only mature 5m contraction sweep/reclaim; "
            "transitional state retains source-like visual liquidity episodes"
        ),
        "slow_common_rules": [
            SLOW_COMMON_AUCTION_RULE,
            SLOW_COMMON_MATERIALITY_RULE,
            SLOW_COMMON_HOLD_RULE,
            PERSISTENT_COMMON_AUCTION_RULE,
            PERSISTENT_ALIGNED_ROUTING_RULE,
        ],
        "persistent_rules": [
            PERSISTENT_CONTINUATION_FORMATION_RULE,
            PERSISTENT_REBALANCE_RULE,
            PERSISTENT_FIRST_RETURN_RULE,
        ],
        "turbulent_rules": [
            TURBULENT_ABSTENTION_RULE,
            TURBULENT_CONTRACTION_RULE,
            TURBULENT_ADVERSE_FLOW_RULE,
            CONTRACTION_OBJECTIVE_RULE,
        ],
        "transitional_rule": TRANSITIONAL_VISUAL_OWNERSHIP_RULE,
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
