#!/usr/bin/env python3
"""Run the source-like direct sweep-OB family through persistent common-auction routing."""
from __future__ import annotations

import json
from pathlib import Path
import sys

from easychart_re1_direct_sweep_ob import (
    DIRECT_SWEEP_OB_PULLBACK_RULE,
    EasyChartRE1DirectSweepOBBundle,
)
from execution_re1_factor_persistence import (
    PERSISTENT_ALIGNED_ROUTING_RULE,
    PERSISTENT_COMMON_AUCTION_RULE,
    TRANSITIONAL_VISUAL_OWNERSHIP_RULE,
    TURBULENT_ABSTENTION_RULE,
    EasyChartRE1PersistentFactorStrategy,
)
import run_mtf_backtest_re1_flow as _flow_runner

_flow_runner._runner.EasyChartRE1NaturalBundle = EasyChartRE1DirectSweepOBBundle
_flow_runner._runner.EasyChartRE1Strategy = EasyChartRE1PersistentFactorStrategy


def _rewrite_metadata(output: Path) -> None:
    values = {
        "candidate": "candidate-easychart_re1_persistent_direct_sweep_ob",
        "decision_policy": (
            "a liquidity-taking flow-validated 15m engulfing OB owns its first defended 5m pullback, "
            "while the latest six common-flow events abstain from turbulence and reject persistent counter-flow"
        ),
        "entry_rule": DIRECT_SWEEP_OB_PULLBACK_RULE,
        "persistent_factor_rules": [
            PERSISTENT_COMMON_AUCTION_RULE,
            PERSISTENT_ALIGNED_ROUTING_RULE,
            TURBULENT_ABSTENTION_RULE,
            TRANSITIONAL_VISUAL_OWNERSHIP_RULE,
        ],
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
