#!/usr/bin/env python3
"""Run confirmed persistent common-flow pullback continuation."""
from __future__ import annotations

import json
from pathlib import Path
import sys

from easychart_re1_persistent_confirmed import (
    PERSISTENT_CONFIRMED_RESPONSE_RULE,
    PERSISTENT_FIRST_ELIGIBLE_OBJECTIVE_RULE,
)
from easychart_re1_persistent_confirmed_fixed import EasyChartRE1FixedConfirmedPersistentBundle
from easychart_re1_persistent_continuation import (
    PERSISTENT_CONTINUATION_FORMATION_RULE,
    PERSISTENT_REBALANCE_RULE,
    PersistentContinuationMarketStrategy,
)
from execution_re1_factor_persistence import (
    PERSISTENT_ALIGNED_ROUTING_RULE,
    PERSISTENT_COMMON_AUCTION_RULE,
    TRANSITIONAL_VISUAL_OWNERSHIP_RULE,
    TURBULENT_ABSTENTION_RULE,
)
import run_mtf_backtest_re1_flow as _flow_runner

_flow_runner._runner.EasyChartRE1NaturalBundle = EasyChartRE1FixedConfirmedPersistentBundle
_flow_runner._runner.EasyChartRE1Strategy = PersistentContinuationMarketStrategy


def _rewrite_metadata(output: Path) -> None:
    values = {
        "candidate": "candidate-easychart_re1_persistent_confirmed",
        "decision_policy": (
            "persistent broad initiative forms flow-validated five-minute OB/FVG pullbacks; "
            "the first touch only arms the setup, a later control-transfer close confirms entry, "
            "and the nearest formation-wave or structural objective with at least one gross R is fixed"
        ),
        "persistent_factor_rules": [
            PERSISTENT_COMMON_AUCTION_RULE,
            PERSISTENT_ALIGNED_ROUTING_RULE,
            TURBULENT_ABSTENTION_RULE,
            TRANSITIONAL_VISUAL_OWNERSHIP_RULE,
        ],
        "persistent_continuation_rules": [
            PERSISTENT_CONTINUATION_FORMATION_RULE,
            PERSISTENT_REBALANCE_RULE,
            PERSISTENT_CONFIRMED_RESPONSE_RULE,
            PERSISTENT_FIRST_ELIGIBLE_OBJECTIVE_RULE,
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
