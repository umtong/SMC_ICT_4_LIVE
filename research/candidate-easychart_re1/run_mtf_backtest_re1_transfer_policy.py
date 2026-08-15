#!/usr/bin/env python3
"""Run responsibility-separated control-transfer policy in one account."""
from __future__ import annotations

import json
from pathlib import Path
import sys

from easychart_re1_transfer_policy import (
    RESPONSIBILITY_SEPARATED_TRANSFER_RULE,
    EasyChartRE1TransferPolicyBundle,
)
from easychart_re1_channel_rejection_hold import (
    CHANNEL_REJECTION_NEXT_BAR_HOLD_RULE,
)
from easychart_re1_flow_progress import ABSORPTION_MIDPOINT_PROGRESS_RULE
import run_mtf_backtest_re1_flow as _flow_runner


_flow_runner._runner.EasyChartRE1NaturalBundle = EasyChartRE1TransferPolicyBundle


def _rewrite_metadata(output: Path) -> None:
    values = {
        "candidate": "candidate-easychart_re1_transfer_policy",
        "decision_policy": (
            "reversal-only phase core; trend-line adverse-flow absorption requires "
            "original 5m sweep midpoint transfer; channel rejection requires next "
            "completed 5m inside hold; original flow-valid 15m OB retained"
        ),
        "transfer_rule_provenance": [
            ABSORPTION_MIDPOINT_PROGRESS_RULE,
            CHANNEL_REJECTION_NEXT_BAR_HOLD_RULE,
            RESPONSIBILITY_SEPARATED_TRANSFER_RULE,
        ],
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
