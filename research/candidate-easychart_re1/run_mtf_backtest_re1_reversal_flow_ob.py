#!/usr/bin/env python3
"""Run reversal phase-flow plus original flow-valid 15m OBs."""
from __future__ import annotations

import json
from pathlib import Path
import sys

from easychart_re1_reversal_flow_ob import EasyChartRE1ReversalFlowOBBundle
from easychart_re1_confluence_flip import ISOLATED_ACCEPTANCE_RESPONSIBILITY_RULE
import run_mtf_backtest_re1_flow as _flow_runner


_flow_runner._runner.EasyChartRE1NaturalBundle = EasyChartRE1ReversalFlowOBBundle


def _rewrite_metadata(output: Path) -> None:
    values = {
        "candidate": "candidate-easychart_re1_reversal_flow_ob",
        "policy": "REVERSAL_PHASE_FLOW_PLUS_ORIGINAL_FLOW_VALIDATED_OB",
        "acceptance_rule": ISOLATED_ACCEPTANCE_RESPONSIBILITY_RULE,
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
