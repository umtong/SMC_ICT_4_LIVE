#!/usr/bin/env python3
"""Run the BTC/ETH-led excluded-altcoin catch-up continuation family."""
from __future__ import annotations

import json
from pathlib import Path
import sys

from easychart_re1_laggard_continuation import (
    LAGGARD_CROSS_IMPACT_RULE,
    LAGGARD_OWN_FLOW_CONFIRMATION_RULE,
    EasyChartRE1LaggardContinuationBundle,
)
from easychart_re1_persistent_confirmed import (
    PERSISTENT_CONFIRMED_RESPONSE_RULE,
    PERSISTENT_FIRST_ELIGIBLE_OBJECTIVE_RULE,
)
from easychart_re1_persistent_continuation import (
    PERSISTENT_CONTINUATION_FORMATION_RULE,
    PERSISTENT_REBALANCE_RULE,
    PersistentContinuationMarketStrategy,
)
from execution_re1_factor_persistence import (
    PERSISTENT_COMMON_AUCTION_RULE,
    TRANSITIONAL_VISUAL_OWNERSHIP_RULE,
    TURBULENT_ABSTENTION_RULE,
)
import run_mtf_backtest_re1_flow as _flow_runner

_flow_runner._runner.EasyChartRE1NaturalBundle = EasyChartRE1LaggardContinuationBundle
_flow_runner._runner.EasyChartRE1Strategy = PersistentContinuationMarketStrategy


def _rewrite_metadata(output: Path) -> None:
    values = {
        "candidate": "candidate-easychart_re1_laggard_continuation",
        "decision_policy": (
            "BTC/ETH-led three-of-four persistent initiative identifies the excluded SOL/XRP; "
            "the laggard must form own flow-valid 5m OB/FVG, first return and later confirmed response"
        ),
        "rules": [
            PERSISTENT_COMMON_AUCTION_RULE,
            LAGGARD_CROSS_IMPACT_RULE,
            LAGGARD_OWN_FLOW_CONFIRMATION_RULE,
            PERSISTENT_CONTINUATION_FORMATION_RULE,
            PERSISTENT_REBALANCE_RULE,
            PERSISTENT_CONFIRMED_RESPONSE_RULE,
            PERSISTENT_FIRST_ELIGIBLE_OBJECTIVE_RULE,
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
