#!/usr/bin/env python3
"""Run the mechanism-routed skilled day-trading policy in one account."""
from __future__ import annotations

import json
from pathlib import Path
import sys

from easychart_re1_skilled_integrated import (
    EasyChartRE1SkilledIntegratedBundle,
    MECHANISM_ROUTED_SKILLED_POLICY_RULE,
    SIMULTANEOUS_EPISODE_OWNERSHIP_RULE,
)
import run_mtf_backtest_re1_flow as _flow_runner


_flow_runner._runner.EasyChartRE1NaturalBundle = EasyChartRE1SkilledIntegratedBundle


def _rewrite_metadata(output: Path) -> None:
    values = {
        "candidate": "candidate-easychart_re1_skilled_integrated",
        "policy": (
            "RESPONSE_CONFIRMED_SIGNIFICANT_OBJECTIVE_REVERSALS_PLUS_"
            "EMBEDDED_RESPONSE_CONFIRMED_ACCEPTANCE_CONTINUATIONS"
        ),
        "mechanism_rules": [
            MECHANISM_ROUTED_SKILLED_POLICY_RULE,
            SIMULTANEOUS_EPISODE_OWNERSHIP_RULE,
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
        _rewrite_metadata(destination)
