#!/usr/bin/env python3
"""Run the ordered-channel phase causal-flow RE1 candidate."""
from __future__ import annotations

import json
from pathlib import Path
import sys

from easychart_re1_flow_phase import (
    PHASE_FLOW_RESPONSIBILITY_RULE,
    EasyChartRE1PhaseFlowBundle,
)
import run_mtf_backtest_re1_flow as _flow_runner


_flow_runner._runner.EasyChartRE1NaturalBundle = EasyChartRE1PhaseFlowBundle


def _rewrite_metadata(output: Path) -> None:
    values = {
        "candidate": "candidate-easychart_re1_flow_phase",
        "phase_flow_policy": (
            "ORDERED_FOUR_POINT_CHANNEL_BOUNDARY; CURRENT_SWEEP_RECLAIM_ABSORPTION; "
            "ACCEPTANCE_FIRST_RETEST_CYCLE; HORIZONTAL_VISUAL_ONLY"
        ),
        "phase_flow_rule_provenance": PHASE_FLOW_RESPONSIBILITY_RULE,
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
