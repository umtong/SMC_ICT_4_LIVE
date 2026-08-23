#!/usr/bin/env python3
"""Run response-confirmed entries with the first significant live objective."""
from __future__ import annotations

import json
from pathlib import Path
import sys

from easychart_re1_significant_response import (
    SIGNIFICANT_RESPONSE_POLICY_RULE,
    EasyChartRE1SignificantResponseBundle,
)
import run_mtf_backtest_re1_flow as flow_runner


flow_runner._runner.EasyChartRE1NaturalBundle = EasyChartRE1SignificantResponseBundle


def rewrite_metadata(output: Path) -> None:
    values = {
        "candidate": "candidate-easychart_re1_significant_response",
        "policy": "RESPONSE_CONFIRMED_AUCTION_PLUS_FIRST_SIGNIFICANT_LIVE_OBJECTIVE",
        "significant_response_rule": SIGNIFICANT_RESPONSE_POLICY_RULE,
        "objective_policy": (
            "NEAREST_LIVE_PREENTRY_5M_15M_OR_CAUSALLY_CONFIRMED_1M_SPAN6_OPPOSING_STRUCTURE"
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
    destination = flow_runner._output_path(sys.argv)
    flow_runner._runner.main()
    if destination is not None:
        flow_runner._rewrite_metadata(destination)
        rewrite_metadata(destination)
