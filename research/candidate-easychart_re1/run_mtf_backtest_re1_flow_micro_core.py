#!/usr/bin/env python3
"""Run the single-family volume-clock micro auction core."""
from __future__ import annotations

import json
from pathlib import Path
import sys

from easychart_re1_flow_micro_core import (
    MICRO_FLOW_CORE_RULE,
    EasyChartRE1VolumeClockMicroCoreBundle,
)
from easychart_re1_flow_volume_clock import (
    FIRST_BUCKET_ONLY_RULE,
    VOLUME_CLOCK_FLOW_RULE,
    VOLUME_CLOCK_MECHANISM_RULE,
)
import run_mtf_backtest_re1_flow as _flow_runner


_flow_runner._runner.EasyChartRE1NaturalBundle = EasyChartRE1VolumeClockMicroCoreBundle


def _rewrite_core_metadata(output: Path) -> None:
    values = {
        "candidate": "candidate-easychart_re1_flow_micro_core",
        "decision_policy": (
            "causal 60m/15m market state -> 15m diagonal/channel liquidity boundary -> "
            "first typical-volume absorption or accepted-break initiative -> natural fixed plan"
        ),
        "executable_families": ["MICRO_FLOW_ONLY"],
        "micro_core_rule_provenance": [
            MICRO_FLOW_CORE_RULE,
            VOLUME_CLOCK_FLOW_RULE,
            FIRST_BUCKET_ONLY_RULE,
            VOLUME_CLOCK_MECHANISM_RULE,
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
        _rewrite_core_metadata(destination)
