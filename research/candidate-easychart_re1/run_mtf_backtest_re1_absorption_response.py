#!/usr/bin/env python3
"""Run the full skilled system with first-response current absorption."""
from __future__ import annotations

import json
from pathlib import Path
import sys

from easychart_re1_absorption_response import (
    CURRENT_ABSORPTION_FIRST_RESPONSE_RULE,
)
from easychart_re1_skilled_continuation import (
    LOCAL_AUCTION_SKILLED_ROUTER_RULE,
)
from easychart_re1_skilled_continuation_response import (
    EasyChartRE1SkilledContinuationPersistentResponseBundle,
)
from execution_re1_flow import EasyChartRE1FlowStrategy
from mtf_data_re1_flow import add_symbol_mtf_flow_data
from plan_event_values_re1 import plan_event_values
import run_mtf_backtest_re1 as runner


runner.EasyChartRE1NaturalBundle = (
    EasyChartRE1SkilledContinuationPersistentResponseBundle
)
EasyChartRE1FlowStrategy._plan_event_values = staticmethod(plan_event_values)
runner.EasyChartRE1Strategy = EasyChartRE1FlowStrategy
runner.add_symbol_mtf_data = add_symbol_mtf_flow_data


def _output_path(argv: list[str]) -> Path | None:
    try:
        return Path(argv[argv.index("--output") + 1])
    except (ValueError, IndexError):
        return None


def rewrite_metadata(output: Path) -> None:
    values = {
        "candidate": "skilled_continuation_absorption_first_response",
        "scenario_engine": (
            "EasyChartRE1SkilledContinuationPersistentResponseBundle"
        ),
        "causal_change": (
            "CURRENT_BOUNDARY_ABSORPTION_EVENT_THEN_FIRST_LATER_RESPONSE"
        ),
        "causal_change_rule": CURRENT_ABSORPTION_FIRST_RESPONSE_RULE,
        "skilled_router_rule": LOCAL_AUCTION_SKILLED_ROUTER_RULE,
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
        rewrite_metadata(destination)
