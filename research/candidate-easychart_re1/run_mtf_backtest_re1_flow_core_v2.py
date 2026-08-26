#!/usr/bin/env python3
"""Run the retest-bound three-family causal-flow RE1 core."""
from __future__ import annotations

import json
from pathlib import Path
import sys

from easychart_re1_flow_core_v2 import (
    FLOW_RETEST_RESPONSIBILITY_RULE,
    EasyChartRE1RetestFlowCoreBundle,
)
import run_mtf_backtest_re1_flow as _flow_runner


_flow_runner._runner.EasyChartRE1NaturalBundle = EasyChartRE1RetestFlowCoreBundle


def _rewrite_metadata(output: Path) -> None:
    values = {
        "candidate": "candidate-easychart_re1_flow_core_v2",
        "flow_core_policy": (
            "THREE_NATURAL_FAMILIES; ACCEPTANCE_INITIATIVE_ONLY_AT_FIRST_RETEST; "
            "REVERSAL_ABSORPTION; VISUAL_ENTRY_REMAINS_OR"
        ),
        "flow_retest_rule_provenance": FLOW_RETEST_RESPONSIBILITY_RULE,
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
