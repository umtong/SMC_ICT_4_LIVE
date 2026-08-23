#!/usr/bin/env python3
"""Run rejection, local OB continuation and horizontal flip response."""
from __future__ import annotations

import json
from pathlib import Path
import sys

from easychart_re1_horizontal_flip_response import (
    HORIZONTAL_FLIP_RESPONSE_RULE,
    HORIZONTAL_FLIP_SIGNIFICANT_OBJECTIVE_RULE,
    EasyChartRE1HorizontalFlipResponseBundle,
)
from easychart_re1_local_auction_continuation import EasyChartRE1LocalAuctionStrategy
import run_mtf_backtest_re1_flow as _flow_runner


_flow_runner._runner.EasyChartRE1NaturalBundle = EasyChartRE1HorizontalFlipResponseBundle
_flow_runner._runner.EasyChartRE1Strategy = EasyChartRE1LocalAuctionStrategy


def _rewrite_metadata(output: Path) -> None:
    values = {
        "candidate": "candidate-easychart_re1_horizontal_flip_response",
        "policy": (
            "RESPONSE_CONFIRMED_REJECTION_OR_LOCAL_FLOW_OB_CONTINUATION_OR_HORIZONTAL_SR_FLIP_FIRST_RESPONSE"
        ),
        "horizontal_flip_rules": [
            HORIZONTAL_FLIP_RESPONSE_RULE,
            HORIZONTAL_FLIP_SIGNIFICANT_OBJECTIVE_RULE,
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
