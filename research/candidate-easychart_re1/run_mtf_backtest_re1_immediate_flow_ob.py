#!/usr/bin/env python3
"""Run the responsible account plus immediate contextual 15m OB entries."""
from __future__ import annotations

import json
from pathlib import Path
import sys

from easychart_re1_immediate_flow_ob import (
    IMMEDIATE_CONTEXT_FLOW_OB_RULE,
    EasyChartRE1ImmediateFlowOBBundle,
)
import run_mtf_backtest_re1_flow as _flow_runner


_flow_runner._runner.EasyChartRE1NaturalBundle = EasyChartRE1ImmediateFlowOBBundle


def _rewrite_metadata(output: Path) -> None:
    values = {
        "candidate": "candidate-easychart_re1_immediate_flow_ob",
        "policy": "RESPONSIBLE_FLOW_CORE_PLUS_IMMEDIATE_CONTEXTUAL_15M_FLOW_OB_CLOSE",
        "immediate_ob_rule": IMMEDIATE_CONTEXT_FLOW_OB_RULE,
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
