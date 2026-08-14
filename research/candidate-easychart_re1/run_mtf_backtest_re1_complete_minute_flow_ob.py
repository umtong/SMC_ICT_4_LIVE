#!/usr/bin/env python3
"""Run flow-valid OBs after all constituent one-minute bars are complete."""
from __future__ import annotations

import json
from pathlib import Path
import sys

from easychart_re1_complete_minute_flow_ob import EasyChartRE1CompleteMinuteFlowOBBundle
import run_mtf_backtest_re1_flow as _flow_runner


_flow_runner._runner.EasyChartRE1NaturalBundle = EasyChartRE1CompleteMinuteFlowOBBundle


def _rewrite_metadata(output: Path) -> None:
    for name in ("metrics.json", "run.json"):
        path = output / name
        if not path.exists():
            continue
        record = json.loads(path.read_text(encoding="utf-8"))
        record.update({
            "candidate": "candidate-easychart_re1_complete_minute_flow_ob",
            "formation_flow_policy": "ALL_FIFTEEN_COMPLETED_CONSTITUENT_MINUTES",
        })
        path.write_text(json.dumps(record, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


if __name__ == "__main__":
    destination = _flow_runner._output_path(sys.argv)
    _flow_runner._runner.main()
    if destination is not None:
        _flow_runner._rewrite_metadata(destination)
        _rewrite_metadata(destination)
