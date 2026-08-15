#!/usr/bin/env python3
"""Run channel abstention plus causal cross-asset factor routing."""
from __future__ import annotations

import json
from pathlib import Path
import sys

from easychart_re1_channel_abstention import (
    CHANNEL_REVERSAL_ABSTENTION_RULE,
    EasyChartRE1ChannelAbstentionBundle,
)
from execution_re1_market_factor import (
    COMMON_FACTOR_MIDPOINT_HYSTERESIS_RULE,
    COUNTERFACTOR_ABSTENTION_RULE,
    CROSS_ASSET_COMMON_INITIATIVE_RULE,
    EasyChartRE1MarketFactorStrategy,
)
import run_mtf_backtest_re1_flow as _flow_runner


_flow_runner._runner.EasyChartRE1NaturalBundle = EasyChartRE1ChannelAbstentionBundle
_flow_runner._runner.EasyChartRE1Strategy = EasyChartRE1MarketFactorStrategy


def _rewrite_metadata(output: Path) -> None:
    values = {
        "candidate": "candidate-easychart_re1_factor_abstention",
        "decision_policy": (
            "channel-edge reversals diagnostic-only; standalone trendline/horizontal/major-swing/"
            "flow-valid-15m-OB plans remain; plans opposite active BTC+ETH plus three-of-four "
            "common one-minute initiative are deferred until common shock midpoint is lost"
        ),
        "channel_abstention_rule": CHANNEL_REVERSAL_ABSTENTION_RULE,
        "market_factor_rules": [
            CROSS_ASSET_COMMON_INITIATIVE_RULE,
            COMMON_FACTOR_MIDPOINT_HYSTERESIS_RULE,
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
