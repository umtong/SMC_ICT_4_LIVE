#!/usr/bin/env python3
"""Run the complete body-aligned EasyChart system in one account."""
from __future__ import annotations

import json
from pathlib import Path
import sys

from easychart_re1_body_aligned_system import (
    MULTITIMEFRAME_BODY_ALIGNMENT_RULE,
    EasyChartRE1BodyAlignedSystem,
)
from easychart_re1_continuation_first_return import CONTINUATION_FIRST_RETURN_ENTRY_RULE
from easychart_re1_continuation_footprint import CONTINUATION_FOOTPRINT_OWNERSHIP_RULE
from easychart_re1_continuation_source_return import CONTINUATION_SOURCE_RETURN_RULE
from easychart_re1_continuation_target_refresh import CONTINUATION_TARGET_LIFECYCLE_RULE
from execution_re1_flow import EasyChartRE1FlowStrategy
from mtf_data_re1_flow import add_symbol_mtf_flow_data
from plan_event_values_re1 import plan_event_values
import run_mtf_backtest_re1 as runner


runner.EasyChartRE1NaturalBundle = EasyChartRE1BodyAlignedSystem
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
        "candidate": "candidate-easychart_re1_body_aligned_system",
        "scenario_engine": "EasyChartRE1BodyAlignedSystem",
        "policy": "COMPLETE_SOURCE_FOOTPRINT_SCENARIOS_ROUTED_BY_COMPLETED_60M_15M_5M_DELIVERY",
        "multitimeframe_body_alignment_rule": MULTITIMEFRAME_BODY_ALIGNMENT_RULE,
        "continuation_target_lifecycle_rule": CONTINUATION_TARGET_LIFECYCLE_RULE,
        "continuation_first_return_entry_rule": CONTINUATION_FIRST_RETURN_ENTRY_RULE,
        "continuation_footprint_ownership_rule": CONTINUATION_FOOTPRINT_OWNERSHIP_RULE,
        "continuation_source_return_rule": CONTINUATION_SOURCE_RETURN_RULE,
        "market_data": "BINANCE_EXTENDED_KLINE_AGGRESSOR_FLOW_PRESERVED",
        "position_contract": "ONE_ACCOUNT_ONE_GLOBAL_POSITION_FIXED_3PCT_NAV_RISK",
    }
    for name in ("metrics.json", "run.json"):
        path = output / name
        if not path.exists():
            continue
        record = json.loads(path.read_text(encoding="utf-8"))
        record.update(values)
        path.write_text(json.dumps(record, ensure_ascii=False, indent=2, sort_keys=True)+"\n",encoding="utf-8")


if __name__ == "__main__":
    destination = _output_path(sys.argv)
    runner.main()
    if destination is not None:
        _rewrite_metadata(destination)
