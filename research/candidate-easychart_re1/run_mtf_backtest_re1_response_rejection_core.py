#!/usr/bin/env python3
"""Run response-confirmed acceptance and current-event OB rejection."""
from __future__ import annotations

import json
from pathlib import Path
import sys

from easychart_re1_response_rejection_core import (
    DECISION_OB_CURRENT_REJECTION_RULE,
    EasyChartRE1ResponseRejectionCoreBundle,
)
import run_mtf_backtest_re1_flow as flow_runner


flow_runner._runner.EasyChartRE1NaturalBundle = (
    EasyChartRE1ResponseRejectionCoreBundle
)


def rewrite_metadata(output: Path) -> None:
    values = {
        "candidate": "candidate-easychart_re1_response_rejection_core",
        "policy": "RESPONSE_CONFIRMED_ACCEPTANCE_PLUS_CURRENT_EVENT_DECISION_OB_REJECTION",
        "decision_ob_rule": DECISION_OB_CURRENT_REJECTION_RULE,
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
    destination = flow_runner._output_path(sys.argv)
    flow_runner._runner.main()
    if destination is not None:
        flow_runner._rewrite_metadata(destination)
        rewrite_metadata(destination)
