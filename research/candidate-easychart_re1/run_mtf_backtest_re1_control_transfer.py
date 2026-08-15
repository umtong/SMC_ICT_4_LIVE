#!/usr/bin/env python3
"""Run the rejection-only decision-frame control-transfer candidate."""
from __future__ import annotations

import json
from pathlib import Path
import sys

from easychart_re1_control_transfer import (
    DECISION_FRAME_CONTROL_TRANSFER_RULE,
    IMPACT_EFFICIENCY_TRANSFER_RULE,
    EasyChartRE1ControlTransferBundle,
)
from easychart_re1_rejection_micro_target import (
    FIRST_UNSPENT_MICRO_FOOTPRINT_TARGET_RULE,
    REJECTION_ONLY_RESPONSIBILITY_RULE,
)
import run_mtf_backtest_re1_flow as _flow_runner


_flow_runner._runner.EasyChartRE1NaturalBundle = EasyChartRE1ControlTransferBundle


def _rewrite_metadata(output: Path) -> None:
    values = {
        "candidate": "candidate-easychart_re1_decision_frame_control_transfer",
        "decision_policy": (
            "visual first-return ownership; flow-only reversal waits for a completed five-minute "
            "control transfer with boundary and interaction-balance reclaim"
        ),
        "entry_rules": [
            REJECTION_ONLY_RESPONSIBILITY_RULE,
            DECISION_FRAME_CONTROL_TRANSFER_RULE,
            IMPACT_EFFICIENCY_TRANSFER_RULE,
        ],
        "objective_rule": FIRST_UNSPENT_MICRO_FOOTPRINT_TARGET_RULE,
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
