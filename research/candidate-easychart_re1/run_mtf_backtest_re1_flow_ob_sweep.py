#!/usr/bin/env python3
"""Run the ordered-channel core plus liquidity-taking flow-validated 15m OBs."""
from __future__ import annotations

import json
from pathlib import Path
import sys

from easychart_re1_flow_ob import (
    FLOW_OB_FIRST_TOUCH_RULE,
    FLOW_VALIDATED_OB_FORMATION_RULE,
)
from easychart_re1_flow_ob_sweep import (
    CAUSAL_SWING_LIQUIDITY_PROXY_RULE,
    LIQUIDITY_TAKING_OB_RULE,
    EasyChartRE1SweepFlowOBBundle,
)
import run_mtf_backtest_re1_flow as _flow_runner


_flow_runner._runner.EasyChartRE1NaturalBundle = EasyChartRE1SweepFlowOBBundle


def _rewrite_metadata(output: Path) -> None:
    values = {
        "candidate": "candidate-easychart_re1_flow_ob_sweep",
        "flow_ob_sweep_policy": (
            "ORDERED_CHANNEL_CORE_PLUS_PREEXISTING_SWING_SWEEP_RECLAIM_"
            "FLOW_VALIDATED_15M_OB_FIRST_RETURN"
        ),
        "flow_ob_sweep_rule_provenance": (
            LIQUIDITY_TAKING_OB_RULE,
            CAUSAL_SWING_LIQUIDITY_PROXY_RULE,
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
