#!/usr/bin/env python3
"""Run the efficient causal first-micro-obstacle objective candidate."""
from __future__ import annotations

import json
from pathlib import Path
import sys

from easychart_re1_channel_abstention import CHANNEL_REVERSAL_ABSTENTION_RULE
from easychart_re1_efficient_objective import (
    PIVOT_ONLY_OBJECTIVE_BOOK_RULE,
    EasyChartRE1EfficientObjectiveBundle,
)
from easychart_re1_fine_objective import FIRST_MICRO_OBSTACLE_RULE
import run_mtf_backtest_re1_flow as _flow_runner


_flow_runner._runner.EasyChartRE1NaturalBundle = EasyChartRE1EfficientObjectiveBundle


def _rewrite_metadata(output: Path) -> None:
    values = {
        "candidate": "candidate-easychart_re1_efficient_objective",
        "decision_policy": (
            "channel reversals diagnostic-only; immediately before entry the nearest still-"
            "unspent confirmed 1m/5m/15m opposing pivot is the immutable first obstacle; "
            "plans with less than the existing 1.0 gross R to that obstacle are rejected"
        ),
        "channel_abstention_rule": CHANNEL_REVERSAL_ABSTENTION_RULE,
        "first_micro_obstacle_rule": FIRST_MICRO_OBSTACLE_RULE,
        "pivot_only_objective_rule": PIVOT_ONLY_OBJECTIVE_BOOK_RULE,
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
