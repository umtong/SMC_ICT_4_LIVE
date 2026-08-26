#!/usr/bin/env python3
"""Run the first-volume-clock causal flow RE1 candidate."""
from __future__ import annotations

import json
from pathlib import Path
import sys

from easychart_re1_flow_volume_clock import (
    FIRST_BUCKET_ONLY_RULE,
    VOLUME_CLOCK_FLOW_RULE,
    VOLUME_CLOCK_MECHANISM_RULE,
    EasyChartRE1VolumeClockFlowBundle,
)
import run_mtf_backtest_re1_flow as _flow_runner


_flow_runner._runner.EasyChartRE1NaturalBundle = EasyChartRE1VolumeClockFlowBundle


def _rewrite_volume_clock_metadata(output: Path) -> None:
    values = {
        "candidate": "candidate-easychart_re1_flow_volume_clock",
        "flow_event_clock": (
            "FIRST_COMPLETED_BUCKET_CONTAINING_ONE_TYPICAL_PRIOR_MINUTE_OF_QUOTE_VOLUME"
        ),
        "flow_event_policy": (
            "EVALUATE_FIRST_BUCKET_ONCE; ACCEPTANCE_USES_CUMULATIVE_INITIATIVE; "
            "REVERSAL_USES_CUMULATIVE_OPPOSING_AGGRESSION_WITH_NONADVERSE_PRICE_PROGRESS"
        ),
        "flow_event_rule_provenance": [
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
        _rewrite_volume_clock_metadata(destination)
