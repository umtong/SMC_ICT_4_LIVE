#!/usr/bin/env python3
"""Run the latest one-minute-completed delivery/balance system."""
from __future__ import annotations

import json
from pathlib import Path
import sys

from easychart_re1_delivery_balance_system_v5 import (
    EasyChartRE1DeliveryBalanceSystemV5Bundle,
)
from easychart_re1_delivery_continuation import TRUE_FIRST_OBSTACLE_RULE
from easychart_re1_delivery_draw_v4 import (
    DELIVERY_FLOW_IMPACT_RULE,
    STRICTLY_LATER_DELIVERY_ENTRY_RULE,
)
from easychart_re1_mature_balance_v2 import SINGLE_USE_BALANCE_LEVEL_RULE
import run_mtf_backtest_re1_flow as flow_runner


flow_runner._runner.EasyChartRE1NaturalBundle = (
    EasyChartRE1DeliveryBalanceSystemV5Bundle
)

ONE_MINUTE_DELIVERY_COMPLETION_RULE = (
    "RESEARCH_HYPOTHESIS:ACTIVE_DELIVERY_ENDS_ON_THE_FIRST_COMPLETED_ONE_MINUTE_"
    "TARGET_OR_SOURCE_INVALIDATION_TOUCH"
)


def rewrite_metadata(output: Path) -> None:
    values = {
        "candidate": "candidate-easychart_re1_delivery_balance_system_v5",
        "policy": "ONE_MINUTE_COMPLETED_DELIVERY_OR_SINGLE_USE_MATURE_BALANCE",
        "delivery_flow_rule": DELIVERY_FLOW_IMPACT_RULE,
        "strict_entry_rule": STRICTLY_LATER_DELIVERY_ENTRY_RULE,
        "first_obstacle_rule": TRUE_FIRST_OBSTACLE_RULE,
        "single_use_balance_rule": SINGLE_USE_BALANCE_LEVEL_RULE,
        "one_minute_delivery_completion_rule": ONE_MINUTE_DELIVERY_COMPLETION_RULE,
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
