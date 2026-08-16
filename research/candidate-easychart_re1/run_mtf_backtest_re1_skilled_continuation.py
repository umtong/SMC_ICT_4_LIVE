#!/usr/bin/env python3
"""Run complete rejection/acceptance plus first-return continuation policy."""
from __future__ import annotations

import json
from pathlib import Path
import sys

from easychart_re1_skilled_continuation_first_return import (
    EasyChartRE1SkilledContinuationFirstReturnBundle,
)
import run_mtf_backtest_re1_flow as _flow_runner


_flow_runner._runner.EasyChartRE1NaturalBundle = (
    EasyChartRE1SkilledContinuationFirstReturnBundle
)


def _rewrite_metadata(output: Path) -> None:
    values = {
        "candidate": "candidate-easychart_re1_skilled_continuation_first_return",
        "policy": (
            "LOCAL_REJECTION_PLUS_LOCAL_OR_H4_ACCEPTANCE_PLUS_FLOW_VALIDATED_"
            "NESTED_INITIATIVE_FIRST_HELD_RETURN"
        ),
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
