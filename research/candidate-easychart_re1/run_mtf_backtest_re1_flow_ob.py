#!/usr/bin/env python3
"""Run the ordered-channel flow core with flow-validated 15m OB decisions."""
from __future__ import annotations

import json
from pathlib import Path
import sys

from easychart_re1_flow_ob import (
    FLOW_OB_FIRST_TOUCH_RULE,
    FLOW_VALIDATED_OB_FORMATION_RULE,
    EasyChartRE1PhaseFlowOBBundle,
)
import run_mtf_backtest_re1_flow as _flow_runner


_flow_runner._runner.EasyChartRE1NaturalBundle = EasyChartRE1PhaseFlowOBBundle


def _rewrite_metadata(output: Path) -> None:
    values = {
        "candidate": "candidate-easychart_re1_flow_ob",
        "flow_ob_policy": (
            "ORDERED_CHANNEL_FLOW_CORE_PLUS_FLOW_VALIDATED_15M_ENGULFING_OB_FIRST_TOUCH"
        ),
        "flow_ob_rule_provenance": (
            FLOW_VALIDATED_OB_FORMATION_RULE,
            FLOW_OB_FIRST_TOUCH_RULE,
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
    destination = _flow_runner._output_path(sys.argv)
    _flow_runner._runner.main()
    if destination is not None:
        _flow_runner._rewrite_metadata(destination)
        _rewrite_metadata(destination)
