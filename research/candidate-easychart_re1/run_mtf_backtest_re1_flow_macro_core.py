#!/usr/bin/env python3
"""Run the independent 60m/15m/1m volume-clock auction core."""
from __future__ import annotations

import json
from pathlib import Path
import sys

from easychart_re1_flow_macro_core import (
    MACRO_FLOW_CORE_RULE,
    EasyChartRE1VolumeClockMacroCoreBundle,
)
import run_mtf_backtest_re1_flow as _flow_runner


_flow_runner._runner.EasyChartRE1NaturalBundle = EasyChartRE1VolumeClockMacroCoreBundle


def _rewrite_macro_metadata(output: Path) -> None:
    values = {
        "candidate": "candidate-easychart_re1_flow_macro_core",
        "decision_policy": (
            "confirmed 60m wick trend-line/channel -> 15m rejection or accepted break -> "
            "first typical 1m quote-volume absorption/initiative -> natural fixed plan"
        ),
        "executable_families": ["MACRO_FLOW_ONLY"],
        "macro_flow_rule_provenance": MACRO_FLOW_CORE_RULE,
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
        _rewrite_macro_metadata(destination)
