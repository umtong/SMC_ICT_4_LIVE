#!/usr/bin/env python3
"""Run the two-mechanism RE1 flow auction core."""
from __future__ import annotations

import json
from pathlib import Path
import sys

from easychart_re1_flow_auction_core import (
    AUCTION_CORE_ROUTING_RULE,
    DECISION_OB_SEQUENCE_RESPONSIBILITY_RULE,
    EasyChartRE1FlowAuctionCoreBundle,
)
from easychart_re1_flow_micro_core import (
    CHANNEL_FADE_MACRO_ALIGNMENT_RULE,
    MICRO_FLOW_CORE_RULE,
)
import run_mtf_backtest_re1_flow as _flow_runner


_flow_runner._runner.EasyChartRE1NaturalBundle = EasyChartRE1FlowAuctionCoreBundle


def _rewrite_core_metadata(output: Path) -> None:
    values = {
        "candidate": "candidate-easychart_re1_flow_auction_core",
        "decision_policy": (
            "one account routes (1) macro-aligned 15m diagonal first-volume-clock flow and "
            "(2) pre-existing high-quality 15m OB absorption-response sequence"
        ),
        "executable_families": [
            "MICRO_VOLUME_CLOCK_FLOW",
            "DECISION_OB_ABSORPTION_SEQUENCE",
        ],
        "auction_core_rule_provenance": [
            AUCTION_CORE_ROUTING_RULE,
            DECISION_OB_SEQUENCE_RESPONSIBILITY_RULE,
            MICRO_FLOW_CORE_RULE,
            CHANNEL_FADE_MACRO_ALIGNMENT_RULE,
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
        _rewrite_core_metadata(destination)
