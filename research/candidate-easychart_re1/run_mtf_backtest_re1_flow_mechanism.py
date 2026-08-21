#!/usr/bin/env python3
"""Run the mechanism-specific causal flow RE1 candidate."""
from __future__ import annotations

import json
from pathlib import Path
import sys

from easychart_re1_flow_mechanism import (
    FLOW_MECHANISM_RESPONSIBILITY_RULE,
    EasyChartRE1MechanismFlowBundle,
)
import run_mtf_backtest_re1_flow as _flow_runner


_flow_runner._runner.EasyChartRE1NaturalBundle = EasyChartRE1MechanismFlowBundle


def _rewrite_mechanism_metadata(output: Path) -> None:
    values = {
        "candidate": "candidate-easychart_re1_flow_mechanism",
        "flow_mechanism_policy": (
            "ACCEPTANCE_USES_INITIATIVE; REJECTION_BOUNCE_ROTATION_USE_ABSORPTION; "
            "VISUAL_OB_FVG_AND_EXACT_RETEST_REMAIN_UNCHANGED"
        ),
        "flow_mechanism_rule_provenance": FLOW_MECHANISM_RESPONSIBILITY_RULE,
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
        _rewrite_mechanism_metadata(destination)
