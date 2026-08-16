#!/usr/bin/env python3
"""Run responsible OB-or-FVG first-return continuation in one account."""
from __future__ import annotations

import json
from pathlib import Path
import sys

from easychart_re1_continuation_first_return import (
    CONTINUATION_FIRST_RETURN_ENTRY_RULE,
)
from easychart_re1_continuation_footprint import (
    CONTINUATION_FOOTPRINT_OWNERSHIP_RULE,
    EasyChartRE1ContinuationFootprintBundle,
)
from easychart_re1_continuation_target_refresh import (
    CONTINUATION_TARGET_LIFECYCLE_RULE,
)
from easychart_re1_displacement_confirmed_auction import (
    BODY_DOMINANT_DELIVERY_RULE,
    DISPLACEMENT_CONFIRMED_AUCTION_RULE,
    WEAK_LOCAL_FAMILY_RETIREMENT_RULE,
)
from execution_re1_flow import EasyChartRE1FlowStrategy
from mtf_data_re1_flow import add_symbol_mtf_flow_data
from plan_event_values_re1 import plan_event_values
import run_mtf_backtest_re1 as runner


runner.EasyChartRE1NaturalBundle = EasyChartRE1ContinuationFootprintBundle
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
        "candidate": "candidate-easychart_re1_continuation_footprint",
        "scenario_engine": "EasyChartRE1ContinuationFootprintBundle",
        "policy": (
            "DISPLACEMENT_CONFIRMED_AUCTION_CORE_PLUS_RESPONSIBLE_OB_OR_FVG_"
            "FIRST_RETURN_CONTINUATION"
        ),
        "body_dominant_delivery_rule": BODY_DOMINANT_DELIVERY_RULE,
        "displacement_confirmed_auction_rule": DISPLACEMENT_CONFIRMED_AUCTION_RULE,
        "weak_local_family_retirement_rule": WEAK_LOCAL_FAMILY_RETIREMENT_RULE,
        "continuation_target_lifecycle_rule": CONTINUATION_TARGET_LIFECYCLE_RULE,
        "continuation_first_return_entry_rule": CONTINUATION_FIRST_RETURN_ENTRY_RULE,
        "continuation_footprint_ownership_rule": CONTINUATION_FOOTPRINT_OWNERSHIP_RULE,
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
