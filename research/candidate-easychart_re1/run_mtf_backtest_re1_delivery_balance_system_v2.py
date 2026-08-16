#!/usr/bin/env python3
"""Run strict matching-scale delivery plus single-use mature balance."""
from __future__ import annotations

import json
from pathlib import Path
import sys

from easychart_re1_delivery_balance_system_v2 import (
    EasyChartRE1DeliveryBalanceSystemV2Bundle,
)
from easychart_re1_delivery_draw_v3 import FRESH_POST_SWEEP_CROSS_RULE
from easychart_re1_mature_balance import MATURE_BALANCE_RULE
from easychart_re1_mature_balance_v2 import (
    SINGLE_USE_BALANCE_LEVEL_RULE,
    UNSPENT_OPPOSITE_BALANCE_TARGET_RULE,
)
import run_mtf_backtest_re1_flow as flow_runner


flow_runner._runner.EasyChartRE1NaturalBundle = (
    EasyChartRE1DeliveryBalanceSystemV2Bundle
)


def rewrite_metadata(output: Path) -> None:
    values = {
        "candidate": "candidate-easychart_re1_delivery_balance_system_v2",
        "policy": "FRESH_EXTERNAL_LIQUIDITY_DELIVERY_OR_SINGLE_USE_MATURE_BALANCE",
        "fresh_shift_rule": FRESH_POST_SWEEP_CROSS_RULE,
        "mature_balance_rule": MATURE_BALANCE_RULE,
        "single_use_balance_rule": SINGLE_USE_BALANCE_LEVEL_RULE,
        "unspent_balance_target_rule": UNSPENT_OPPOSITE_BALANCE_TARGET_RULE,
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
    destination = flow_runner._output_path(sys.argv)
    flow_runner._runner.main()
    if destination is not None:
        flow_runner._rewrite_metadata(destination)
        rewrite_metadata(destination)
