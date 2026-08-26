#!/usr/bin/env python3
"""Run the lean matching-scale delivery/channel day-trade core."""
from __future__ import annotations

import json
from pathlib import Path
import sys

from easychart_re1_delivery_channel_acceptance import (
    DRAW_ALIGNED_CHANNEL_ACCEPTANCE_RULE,
)
from easychart_re1_delivery_channel_acceptance_v3 import (
    PROXIMAL_DELIVERY_CONTINUATION_RULE,
)
from easychart_re1_delivery_channel_core import MATURE_BALANCE_DEFERRED_RULE
from easychart_re1_delivery_channel_core_v2 import (
    ACCEPTED_BREAK_EXECUTION_RESPONSIBILITY_RULE,
    EasyChartRE1DeliveryChannelCoreV2Bundle,
)
from easychart_re1_delivery_continuation_cisd import (
    POST_TOUCH_INTERNAL_SHIFT_RULE,
)
from easychart_re1_delivery_continuation_v2 import (
    COMPLETE_MICRO_FIRST_OBSTACLE_RULE,
)
from easychart_re1_delivery_draw_v4 import DELIVERY_FLOW_IMPACT_RULE
from easychart_re1_delivery_draw_v6 import (
    MECHANISM_SPECIFIC_ACTIVE_FLOW_RULE,
)
import run_mtf_backtest_re1_flow as flow_runner


flow_runner._runner.EasyChartRE1NaturalBundle = (
    EasyChartRE1DeliveryChannelCoreV2Bundle
)


def rewrite_metadata(output: Path) -> None:
    values = {
        "candidate": "candidate-easychart_re1_delivery_channel_core_v2",
        "policy": "LEAN_MECHANISM_SEPARATED_DELIVERY_CHANNEL_SR_FLIP_OR_SWEEP_CISD_PULLBACK",
        "delivery_flow_rule": DELIVERY_FLOW_IMPACT_RULE,
        "delivery_mechanism_rule": MECHANISM_SPECIFIC_ACTIVE_FLOW_RULE,
        "channel_acceptance_rule": DRAW_ALIGNED_CHANNEL_ACCEPTANCE_RULE,
        "accepted_break_responsibility_rule": ACCEPTED_BREAK_EXECUTION_RESPONSIBILITY_RULE,
        "continuation_location_rule": PROXIMAL_DELIVERY_CONTINUATION_RULE,
        "continuation_target_rule": COMPLETE_MICRO_FIRST_OBSTACLE_RULE,
        "continuation_response_rule": POST_TOUCH_INTERNAL_SHIFT_RULE,
        "balance_policy": MATURE_BALANCE_DEFERRED_RULE,
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
