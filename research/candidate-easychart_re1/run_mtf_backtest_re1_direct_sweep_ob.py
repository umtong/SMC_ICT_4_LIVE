#!/usr/bin/env python3
"""Run direct first-pullback entries over liquidity-taking flow-valid OBs."""
from __future__ import annotations

import json
from pathlib import Path
import sys

from easychart_re1_direct_sweep_ob import (
    DIRECT_SWEEP_OB_PULLBACK_RULE,
    EasyChartRE1DirectSweepOBBundle,
)
import run_mtf_backtest_re1_flow as _flow_runner


_flow_runner._runner.EasyChartRE1NaturalBundle = EasyChartRE1DirectSweepOBBundle


def _rewrite_metadata(output: Path) -> None:
    values = {
        "candidate": "candidate-easychart_re1_direct_sweep_ob",
        "policy": "SINGLE_OWNER_REVERSAL_CORE_PLUS_DIRECT_AND_DELAYED_SWEEP_VALID_OB",
        "direct_sweep_ob_rule": DIRECT_SWEEP_OB_PULLBACK_RULE,
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
