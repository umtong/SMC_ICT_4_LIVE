#!/usr/bin/env python3
"""Run the ordered-phase flow candidate with direct hold initiative."""
from __future__ import annotations

import json
from pathlib import Path
import sys

from easychart_re1_flow_phase_breakout import (
    DIRECT_HOLD_INITIATIVE_RULE,
    EasyChartRE1PhaseBreakoutFlowBundle,
)
import run_mtf_backtest_re1_flow as _flow_runner


_flow_runner._runner.EasyChartRE1NaturalBundle = EasyChartRE1PhaseBreakoutFlowBundle


def _rewrite_metadata(output: Path) -> None:
    values = {
        "candidate": "candidate-easychart_re1_flow_phase_breakout",
        "direct_breakout_policy": (
            "ORDERED_PHASE; BREAK_BAR_AND_REQUIRED_HOLD_INITIATIVE; "
            "FIRST_RETEST_AND_VISUAL_PATHS_REMAIN_OR"
        ),
        "direct_breakout_rule_provenance": DIRECT_HOLD_INITIATIVE_RULE,
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
