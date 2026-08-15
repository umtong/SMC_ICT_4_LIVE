#!/usr/bin/env python3
"""Run channel abstention with five-minute common displacement routing."""
from __future__ import annotations

import json
from pathlib import Path
import sys

from easychart_re1_channel_abstention import (
    CHANNEL_REVERSAL_ABSTENTION_RULE,
    EasyChartRE1ChannelAbstentionBundle,
)
from execution_re1_market_displacement import (
    FIVE_MINUTE_COMMON_DISPLACEMENT_RULE,
    FIVE_MINUTE_FACTOR_HYSTERESIS_RULE,
    EasyChartRE1MarketDisplacementStrategy,
)
from execution_re1_market_factor import COUNTERFACTOR_ABSTENTION_RULE
import run_mtf_backtest_re1_flow as _flow_runner


_flow_runner._runner.EasyChartRE1NaturalBundle = EasyChartRE1ChannelAbstentionBundle
_flow_runner._runner.EasyChartRE1Strategy = EasyChartRE1MarketDisplacementStrategy


def _rewrite_metadata(output: Path) -> None:
    values = {
        "candidate": "candidate-easychart_re1_displacement_abstention",
        "decision_policy": (
            "channel reversals diagnostic-only; counter-direction plans deferred only while "
            "BTC+ETH and three-of-four aligned above-prior-60m completed 5m displacement "
            "continues to hold its cross-asset event midpoints"
        ),
        "channel_abstention_rule": CHANNEL_REVERSAL_ABSTENTION_RULE,
        "market_displacement_rules": [
            FIVE_MINUTE_COMMON_DISPLACEMENT_RULE,
            FIVE_MINUTE_FACTOR_HYSTERESIS_RULE,
            COUNTERFACTOR_ABSTENTION_RULE,
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
