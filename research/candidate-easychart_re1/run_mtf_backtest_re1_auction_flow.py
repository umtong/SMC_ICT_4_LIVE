#!/usr/bin/env python3
"""Run the three-family auction-cycle flow RE1 candidate."""
from __future__ import annotations

import json
from pathlib import Path
import sys

from easychart_re1_auction_flow import (
    AUCTION_CYCLE_FLOW_RULE,
    FLOW_NOT_GLOBAL_GATE_RULE,
    EasyChartRE1AuctionFlowBundle,
)
import run_mtf_backtest_re1_flow as _flow_runner


_flow_runner._runner.EasyChartRE1NaturalBundle = EasyChartRE1AuctionFlowBundle


def _rewrite_metadata(output: Path) -> None:
    values = {
        "candidate": "candidate-easychart_re1_auction_flow",
        "auction_flow_policy": (
            "ACCEPTANCE_BREAK_HOLD_PLUS_FIRST_RETEST_RESPONSE; "
            "REVERSAL_CONTIGUOUS_BOUNDARY_ABSORPTION; VISUAL_ENTRY_REMAINS_OR"
        ),
        "auction_flow_rule_provenance": (
            AUCTION_CYCLE_FLOW_RULE,
            FLOW_NOT_GLOBAL_GATE_RULE,
        ),
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
