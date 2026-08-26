#!/usr/bin/env python3
"""Run the integrated liquidity-sweep flow-OB system with one entry evidence owner."""
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
)
from easychart_re1_flow_ob_sweep_responsibility import (
    SINGLE_ENTRY_EVIDENCE_OWNER_RULE,
    EasyChartRE1ResponsibleSweepFlowOBBundle,
)
import run_mtf_backtest_re1_flow as _flow_runner


_flow_runner._runner.EasyChartRE1NaturalBundle = EasyChartRE1ResponsibleSweepFlowOBBundle


def _rewrite_metadata(output: Path) -> None:
    values = {
        "candidate": "candidate-easychart_re1_flow_ob_sweep_responsibility",
        "policy": (
            "ORDERED_CHANNEL_PLUS_LIQUIDITY_SWEEP_FLOW_OB_WITH_SINGLE_ENTRY_EVIDENCE_OWNER"
        ),
        "rule_provenance": (
            SINGLE_ENTRY_EVIDENCE_OWNER_RULE,
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
