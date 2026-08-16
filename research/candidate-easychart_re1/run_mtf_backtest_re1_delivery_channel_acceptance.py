#!/usr/bin/env python3
"""Run draw-aligned channel acceptance with delivery and mature balance."""
from __future__ import annotations

import json
from pathlib import Path
import sys

from easychart_re1_delivery_channel_acceptance import (
    CHANNEL_ACCEPTANCE_EPISODE_PRIORITY_RULE,
    DRAW_ALIGNED_CHANNEL_ACCEPTANCE_RULE,
    EasyChartRE1DeliveryChannelAcceptanceBundle,
)
from easychart_re1_delivery_continuation import TRUE_FIRST_OBSTACLE_RULE
from easychart_re1_delivery_draw_v4 import DELIVERY_FLOW_IMPACT_RULE
from easychart_re1_mature_balance_v2 import SINGLE_USE_BALANCE_LEVEL_RULE
import run_mtf_backtest_re1_flow as flow_runner


flow_runner._runner.EasyChartRE1NaturalBundle = (
    EasyChartRE1DeliveryChannelAcceptanceBundle
)


def rewrite_metadata(output: Path) -> None:
    values = {
        "candidate": "candidate-easychart_re1_delivery_channel_acceptance",
        "policy": "FLOW_VALIDATED_DELIVERY_CHANNEL_SR_FLIP_OR_CONTINUATION_OR_MATURE_BALANCE",
        "delivery_flow_rule": DELIVERY_FLOW_IMPACT_RULE,
        "channel_acceptance_rule": DRAW_ALIGNED_CHANNEL_ACCEPTANCE_RULE,
        "channel_episode_priority_rule": CHANNEL_ACCEPTANCE_EPISODE_PRIORITY_RULE,
        "continuation_target_rule": TRUE_FIRST_OBSTACLE_RULE,
        "single_use_balance_rule": SINGLE_USE_BALANCE_LEVEL_RULE,
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
