#!/usr/bin/env python3
"""Run the executable complete EasyChart base policy used by ML3."""
from __future__ import annotations

import json
from pathlib import Path
import sys

from easychart_re1_complete_bot_policy import COMPLETE_OPPORTUNITY_ROUTER_RULE
from easychart_re1_complete_bot_policy_v2 import UNIFIED_CONTINUATION_CONTEXT_RULE
from easychart_re1_ml3_base_policy import (
    LOCAL_ENGINE_TIMEFRAME_POLICY,
    EasyChartRE1ML3BasePolicyBundle,
)
from easychart_re1_local_auction_continuation import EasyChartRE1LocalAuctionStrategy
import run_mtf_backtest_re1_flow as _flow_runner


_flow_runner._runner.EasyChartRE1NaturalBundle = EasyChartRE1ML3BasePolicyBundle
_flow_runner._runner.EasyChartRE1Strategy = EasyChartRE1LocalAuctionStrategy


def _rewrite_metadata(output: Path) -> None:
    values = {
        "candidate": "candidate-easychart_re1_ml3_base",
        "policy": "COMPLETE_EASYCHART_OPPORTUNITY_SET_WITH_EXECUTABLE_LOCAL_TIMEFRAME_CONTRACTS",
        "complete_policy_rule": COMPLETE_OPPORTUNITY_ROUTER_RULE,
        "unified_continuation_context_rule": UNIFIED_CONTINUATION_CONTEXT_RULE,
        "local_engine_timeframe_policy": LOCAL_ENGINE_TIMEFRAME_POLICY,
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
