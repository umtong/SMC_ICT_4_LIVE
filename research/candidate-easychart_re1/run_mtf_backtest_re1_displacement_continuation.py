#!/usr/bin/env python3
"""Run five-minute common displacement routing and continuation family."""
from __future__ import annotations

import json
from pathlib import Path
import sys

from easychart_re1_channel_abstention import CHANNEL_REVERSAL_ABSTENTION_RULE
from easychart_re1_factor_continuation import (
    FACTOR_CONTINUATION_FIRST_RETURN_RULE,
    FACTOR_CONTINUATION_FORMATION_RULE,
    EasyChartRE1FactorContinuationBundle,
)
from execution_re1_factor_continuation_v2 import (
    FactorDisplacementContinuationMarketStrategy,
)
from execution_re1_market_displacement import (
    FIVE_MINUTE_COMMON_DISPLACEMENT_RULE,
    FIVE_MINUTE_FACTOR_HYSTERESIS_RULE,
)
from execution_re1_market_factor import COUNTERFACTOR_ABSTENTION_RULE
import run_mtf_backtest_re1_flow as _flow_runner


_flow_runner._runner.EasyChartRE1NaturalBundle = EasyChartRE1FactorContinuationBundle
_flow_runner._runner.EasyChartRE1Strategy = FactorDisplacementContinuationMarketStrategy


def _rewrite_metadata(output: Path) -> None:
    values = {
        "candidate": "candidate-easychart_re1_displacement_continuation",
        "decision_policy": (
            "channel reversals diagnostic-only; counterfactor plans deferred during broad "
            "completed-5m displacement; aligned flow-valid 5m OB first-return continuation "
            "family competes in the same one-position account"
        ),
        "channel_abstention_rule": CHANNEL_REVERSAL_ABSTENTION_RULE,
        "market_displacement_rules": [
            FIVE_MINUTE_COMMON_DISPLACEMENT_RULE,
            FIVE_MINUTE_FACTOR_HYSTERESIS_RULE,
            COUNTERFACTOR_ABSTENTION_RULE,
        ],
        "factor_continuation_rules": [
            FACTOR_CONTINUATION_FORMATION_RULE,
            FACTOR_CONTINUATION_FIRST_RETURN_RULE,
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
