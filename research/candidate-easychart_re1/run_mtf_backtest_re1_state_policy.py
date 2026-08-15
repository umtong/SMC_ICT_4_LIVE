#!/usr/bin/env python3
"""Run the integrated persistent/turbulent/transitional EasyChart RE1 policy."""
from __future__ import annotations

import json
from pathlib import Path
import sys

from easychart_re1_state_policy import EasyChartRE1StatePolicyBundle
from easychart_re1_persistent_confirmed import (
    PERSISTENT_CONFIRMED_RESPONSE_RULE,
    PERSISTENT_FIRST_ELIGIBLE_OBJECTIVE_RULE,
)
from easychart_re1_persistent_continuation import (
    PERSISTENT_CONTINUATION_FORMATION_RULE,
    PERSISTENT_REBALANCE_RULE,
)
from easychart_re1_turbulent_box import (
    COMPLETE_CONTRACTION_BOX_RULE,
    LOCAL_BOX_SELECTION_RULE,
)
from easychart_re1_turbulent_contraction import (
    CONTRACTION_OBJECTIVE_RULE,
    TURBULENT_ADVERSE_FLOW_RULE,
    FullAuctionStateStrategy,
)
from execution_re1_factor_persistence import (
    PERSISTENT_ALIGNED_ROUTING_RULE,
    PERSISTENT_COMMON_AUCTION_RULE,
    TRANSITIONAL_VISUAL_OWNERSHIP_RULE,
    TURBULENT_ABSTENTION_RULE,
)
import run_mtf_backtest_re1_flow as _flow_runner

_flow_runner._runner.EasyChartRE1NaturalBundle = EasyChartRE1StatePolicyBundle
_flow_runner._runner.EasyChartRE1Strategy = FullAuctionStateStrategy


def _rewrite_metadata(output: Path) -> None:
    values = {
        "candidate": "candidate-easychart_re1_state_policy",
        "decision_policy": (
            "persistent common initiative owns confirmed flow-validated 5m footprint pullbacks; "
            "turbulent common flow owns complete paired-box sweep/reclaims; transitional states "
            "retain only visual decision-OB and major-liquidity episodes"
        ),
        "persistent_rules": [
            PERSISTENT_COMMON_AUCTION_RULE,
            PERSISTENT_ALIGNED_ROUTING_RULE,
            PERSISTENT_CONTINUATION_FORMATION_RULE,
            PERSISTENT_REBALANCE_RULE,
            PERSISTENT_CONFIRMED_RESPONSE_RULE,
            PERSISTENT_FIRST_ELIGIBLE_OBJECTIVE_RULE,
        ],
        "turbulent_rules": [
            TURBULENT_ABSTENTION_RULE,
            COMPLETE_CONTRACTION_BOX_RULE,
            LOCAL_BOX_SELECTION_RULE,
            TURBULENT_ADVERSE_FLOW_RULE,
            CONTRACTION_OBJECTIVE_RULE,
        ],
        "transitional_rule": TRANSITIONAL_VISUAL_OWNERSHIP_RULE,
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
