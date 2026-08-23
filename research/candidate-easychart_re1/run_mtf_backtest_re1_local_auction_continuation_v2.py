#!/usr/bin/env python3
"""Run flow-confirmed local continuation with response-confirmed rejection."""
from __future__ import annotations

import json
from pathlib import Path
import sys

from easychart_re1_local_auction_continuation import (
    COMMON_FACTOR_VETO_ONLY_RULE,
    LOCAL_AUCTION_CONTINUATION_RULE,
    SIGNIFICANT_CONTINUATION_OBJECTIVE_RULE,
    EasyChartRE1LocalAuctionStrategy,
)
from easychart_re1_local_auction_continuation_v2 import (
    LOCAL_CONTINUATION_RESPONSE_FLOW_RULE,
    EasyChartRE1LocalAuctionContinuationV2Bundle,
)
import run_mtf_backtest_re1_flow as _flow_runner


_flow_runner._runner.EasyChartRE1NaturalBundle = EasyChartRE1LocalAuctionContinuationV2Bundle
_flow_runner._runner.EasyChartRE1Strategy = EasyChartRE1LocalAuctionStrategy


def _rewrite_metadata(output: Path) -> None:
    values = {
        "candidate": "candidate-easychart_re1_local_auction_continuation_v2",
        "policy": (
            "RESPONSE_CONFIRMED_REJECTION_OR_LOCAL_15M_BOS_FLOW_VALIDATED_5M_OB_FIRST_RETURN_WITH_RESPONSE_FLOW_TRANSFER"
        ),
        "local_auction_rules": [
            LOCAL_AUCTION_CONTINUATION_RULE,
            COMMON_FACTOR_VETO_ONLY_RULE,
            SIGNIFICANT_CONTINUATION_OBJECTIVE_RULE,
            LOCAL_CONTINUATION_RESPONSE_FLOW_RULE,
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
