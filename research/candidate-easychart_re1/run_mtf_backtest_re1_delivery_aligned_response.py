#!/usr/bin/env python3
"""Run significant-response entries with completed-hour rejection ownership."""
from __future__ import annotations

import json
from pathlib import Path
import sys

from easychart_re1_delivery_aligned_response import (
    COMPLETED_HOURLY_DELIVERY_OWNERSHIP_RULE,
    EasyChartRE1DeliveryAlignedResponseBundle,
)
from execution_re1_flow import EasyChartRE1FlowStrategy
from mtf_data_re1_flow import add_symbol_mtf_flow_data
from plan_event_values_re1 import plan_event_values
import run_mtf_backtest_re1 as runner


runner.EasyChartRE1NaturalBundle = EasyChartRE1DeliveryAlignedResponseBundle
EasyChartRE1FlowStrategy._plan_event_values = staticmethod(plan_event_values)
runner.EasyChartRE1Strategy = EasyChartRE1FlowStrategy
runner.add_symbol_mtf_data = add_symbol_mtf_flow_data


def _output_path(argv: list[str]) -> Path | None:
    try:
        return Path(argv[argv.index("--output") + 1])
    except (ValueError, IndexError):
        return None


def _rewrite_metadata(output: Path) -> None:
    values = {
        "candidate": "candidate-easychart_re1_delivery_aligned_response",
        "scenario_engine": "EasyChartRE1DeliveryAlignedResponseBundle",
        "policy": "SIGNIFICANT_RESPONSE_WITH_COMPLETED_HOURLY_MICRO_REJECTION_OWNERSHIP",
        "hourly_delivery_rule": COMPLETED_HOURLY_DELIVERY_OWNERSHIP_RULE,
        "market_data": "BINANCE_EXTENDED_KLINE_AGGRESSOR_FLOW_PRESERVED",
        "position_contract": "ONE_ACCOUNT_ONE_GLOBAL_POSITION_FIXED_3PCT_NAV_RISK",
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
    destination = _output_path(sys.argv)
    runner.main()
    if destination is not None:
        _rewrite_metadata(destination)
