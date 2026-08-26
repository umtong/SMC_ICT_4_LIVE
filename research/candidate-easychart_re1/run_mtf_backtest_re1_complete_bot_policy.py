#!/usr/bin/env python3
"""Run the complete contextual EasyChart opportunity policy."""
from __future__ import annotations

import json
from pathlib import Path
import sys

from easychart_re1_complete_bot_policy import (
    COMPLETE_OPPORTUNITY_ROUTER_RULE,
    EasyChartRE1CompleteBotPolicyBundle,
)
from easychart_re1_efficient_pullback_context import EFFICIENT_PULLBACK_BROAD_CONTEXT_RULE
from easychart_re1_macro_trend_pullback import (
    MACRO_TREND_PULLBACK_LIFECYCLE_RULE,
    MACRO_TREND_PULLBACK_RULE,
)
from easychart_re1_local_auction_continuation import EasyChartRE1LocalAuctionStrategy
import run_mtf_backtest_re1_flow as _flow_runner


_flow_runner._runner.EasyChartRE1NaturalBundle = EasyChartRE1CompleteBotPolicyBundle
_flow_runner._runner.EasyChartRE1Strategy = EasyChartRE1LocalAuctionStrategy


def _rewrite_metadata(output: Path) -> None:
    values = {
        "candidate": "candidate-easychart_re1_complete_bot_policy",
        "policy": (
            "RESPONSIBLE_REJECTION_OB_FVG_HORIZONTAL_CONTEXTUAL_LOCAL_PULLBACK_RESIDUAL_MACRO_PULLBACK_AND_DIAGONAL_ACCEPTANCE"
        ),
        "complete_policy_rules": [
            EFFICIENT_PULLBACK_BROAD_CONTEXT_RULE,
            MACRO_TREND_PULLBACK_RULE,
            MACRO_TREND_PULLBACK_LIFECYCLE_RULE,
            COMPLETE_OPPORTUNITY_ROUTER_RULE,
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
