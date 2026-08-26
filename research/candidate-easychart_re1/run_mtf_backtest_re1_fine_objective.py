#!/usr/bin/env python3
"""Run the first-causal-micro-obstacle objective candidate."""
from __future__ import annotations

import json
from pathlib import Path
import sys

from easychart_re1_fine_objective import FIRST_MICRO_OBSTACLE_RULE, EasyChartRE1FineObjectiveBundle
import run_mtf_backtest_re1_flow as _flow_runner


_flow_runner._runner.EasyChartRE1NaturalBundle = EasyChartRE1FineObjectiveBundle


def _rewrite_metadata(output: Path) -> None:
    values = {
        "candidate": "candidate-easychart_re1_fine_objective",
        "target_policy": "FIRST_CAUSAL_1M_5M_OR_15M_OPPOSING_STRUCTURE",
        "target_rule": FIRST_MICRO_OBSTACLE_RULE,
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
