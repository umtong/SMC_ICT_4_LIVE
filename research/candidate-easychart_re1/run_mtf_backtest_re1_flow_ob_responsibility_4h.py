#!/usr/bin/env python3
"""Run responsible flow-OB execution with a four-hour top-down context router."""
from __future__ import annotations

import json
from pathlib import Path
import sys

from easychart_re1_flow_ob_responsibility_4h import EasyChartRE1ResponsibleFlowOB4HBundle
from mtf_data_re1_flow_4h import add_symbol_mtf_flow_data_4h
import run_mtf_backtest_re1_flow as _flow_runner

_flow_runner._runner.EasyChartRE1NaturalBundle = EasyChartRE1ResponsibleFlowOB4HBundle
_flow_runner._runner.add_symbol_mtf_data = add_symbol_mtf_flow_data_4h
_flow_runner._runner.EasyChartRE1Strategy.HIGHER_MINUTES = 240


def _rewrite_metadata(output: Path) -> None:
    values = {
        "candidate": "candidate-easychart_re1_flow_ob_responsibility_4h_context",
        "scale_policy": "4H_CONTEXT_ROUTER_PLUS_15_5_1_RESPONSIBLE_FLOW_OB_FAMILIES",
        "context_router_policy": "four-hour close-confirmed wick-swing BOS direction with same-side four-hour decision-area exception",
        "research_change": "ONLY_CONTEXT_HORIZON_CHANGED_FROM_60M_TO_240M",
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
