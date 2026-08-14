#!/usr/bin/env python3
"""Run same-body multi-structure breakout-stack S/R flips."""
from __future__ import annotations

import json
from pathlib import Path
import sys

from easychart_re1_confluence_flip import (
    CONFLUENCE_FLIP_RULE,
    ISOLATED_ACCEPTANCE_RESPONSIBILITY_RULE,
)
from easychart_re1_confluence_flip_stack import EasyChartRE1BreakoutStackConfluenceBundle
import run_mtf_backtest_re1_flow as _flow_runner


_flow_runner._runner.EasyChartRE1NaturalBundle = EasyChartRE1BreakoutStackConfluenceBundle


def _rewrite_metadata(output: Path) -> None:
    values = {
        "candidate": "candidate-easychart_re1_confluence_flip_stack",
        "policy": "REVERSAL_CORE_PLUS_SAME_BODY_MULTI_STRUCTURE_SR_FLIP",
        "rules": (CONFLUENCE_FLIP_RULE, ISOLATED_ACCEPTANCE_RESPONSIBILITY_RULE),
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
