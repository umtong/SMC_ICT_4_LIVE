#!/usr/bin/env python3
"""Run rejection-only flow-valid 15m decision OB routing."""
from __future__ import annotations

import json
from pathlib import Path
import sys

from easychart_re1_channel_abstention import CHANNEL_REVERSAL_ABSTENTION_RULE
from easychart_re1_decision_ob_rejection import (
    FLOW_VALIDATED_OB_REJECTION_ONLY_RULE,
    EasyChartRE1DecisionOBRejectionBundle,
)
import run_mtf_backtest_re1_flow as _flow_runner


_flow_runner._runner.EasyChartRE1NaturalBundle = EasyChartRE1DecisionOBRejectionBundle


def _rewrite_metadata(output: Path) -> None:
    values = {
        "candidate": "candidate-easychart_re1_decision_ob_rejection",
        "decision_policy": (
            "channel reversals diagnostic-only; original flow-valid 15m OB birth retained; "
            "later flow-valid OB executes sweep/reclaim rejection only and blind bounce is diagnostic"
        ),
        "channel_abstention_rule": CHANNEL_REVERSAL_ABSTENTION_RULE,
        "decision_ob_rejection_rule": FLOW_VALIDATED_OB_REJECTION_ONLY_RULE,
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
