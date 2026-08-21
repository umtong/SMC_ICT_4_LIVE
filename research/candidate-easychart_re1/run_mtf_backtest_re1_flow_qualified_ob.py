#!/usr/bin/env python3
"""Run the mechanism-flow-qualified immediate-OB RE1 candidate."""
from __future__ import annotations

import json
from pathlib import Path
import sys

from easychart_re1_flow_qualified_ob import (
    FLOW_QUALIFIED_IMMEDIATE_OB_RULE,
    EasyChartRE1FlowQualifiedOBBundle,
)
import run_mtf_backtest_re1_flow as _flow_runner


_flow_runner._runner.EasyChartRE1NaturalBundle = EasyChartRE1FlowQualifiedOBBundle


def _rewrite_qualified_metadata(output: Path) -> None:
    values = {
        "candidate": "candidate-easychart_re1_flow_qualified_ob",
        "immediate_ob_policy": (
            "HIGH_QUALITY_ENGULFING_OB_ENTERS_IMMEDIATELY_ONLY_WITH_SCENARIO_APPROPRIATE_FLOW; "
            "OTHERWISE_THE_SAME_OB_REMAINS_ELIGIBLE_FOR_DEPARTURE_FIRST_RETEST_RESPONSE"
        ),
        "immediate_ob_rule_provenance": FLOW_QUALIFIED_IMMEDIATE_OB_RULE,
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
        _rewrite_qualified_metadata(destination)
