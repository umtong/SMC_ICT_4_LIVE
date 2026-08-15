#!/usr/bin/env python3
"""Run the material-response flow-substitution candidate."""
from __future__ import annotations

import json
from pathlib import Path
import sys

from easychart_re1_channel_abstention import CHANNEL_REVERSAL_ABSTENTION_RULE
from easychart_re1_material_response import (
    MATERIAL_RESPONSE_SUBSTITUTION_RULE,
    EasyChartRE1MaterialResponseBundle,
)
import run_mtf_backtest_re1_flow as _flow_runner


_flow_runner._runner.EasyChartRE1NaturalBundle = EasyChartRE1MaterialResponseBundle


def _rewrite_metadata(output: Path) -> None:
    values = {
        "candidate": "candidate-easychart_re1_material_response",
        "decision_policy": (
            "channel reversals diagnostic-only; a current absorption may replace a missing "
            "visual footprint only when its completed 1m price response is at least the "
            "causal previous-60-minute median body; visual first-return entries unchanged"
        ),
        "channel_abstention_rule": CHANNEL_REVERSAL_ABSTENTION_RULE,
        "material_response_rule": MATERIAL_RESPONSE_SUBSTITUTION_RULE,
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
