#!/usr/bin/env python3
"""Run strictly causal matching-scale liquidity delivery."""
from __future__ import annotations

import json
from pathlib import Path
import sys

from easychart_re1_delivery_draw_v2 import (
    ACTIVATION_BAR_UNSPENT_DRAW_RULE,
    STRICTLY_LATER_INTERNAL_SHIFT_RULE,
)
from easychart_re1_delivery_system import DELIVERY_RESPONSIBILITY_ROUTER_RULE
from easychart_re1_delivery_system_v2 import EasyChartRE1DeliverySystemV2Bundle
import run_mtf_backtest_re1_flow as flow_runner


flow_runner._runner.EasyChartRE1NaturalBundle = EasyChartRE1DeliverySystemV2Bundle


def rewrite_metadata(output: Path) -> None:
    values = {
        "candidate": "candidate-easychart_re1_delivery_system_v2",
        "policy": "STRICT_CAUSAL_MATCHING_SCALE_LIQUIDITY_DELIVERY",
        "delivery_router_rule": DELIVERY_RESPONSIBILITY_ROUTER_RULE,
        "strict_shift_rule": STRICTLY_LATER_INTERNAL_SHIFT_RULE,
        "unspent_draw_rule": ACTIVATION_BAR_UNSPENT_DRAW_RULE,
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
