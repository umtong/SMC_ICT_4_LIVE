#!/usr/bin/env python3
"""Run the causal aggressor-flow RE1 candidate in one continuous account."""
from __future__ import annotations

import json
from pathlib import Path
import sys

import run_mtf_backtest_re1 as _runner
from easychart_re1_flow import (
    BINANCE_AGGRESSOR_FLOW_RULE,
    CAUSAL_FLOW_BASELINE_RULE,
    FLOW_BREAKOUT_RULE,
    FLOW_SUBSTITUTION_RULE,
    EasyChartRE1FlowBundle,
)
from execution_re1_flow import EasyChartRE1FlowStrategy
from mtf_data_re1_flow import add_symbol_mtf_flow_data


_runner.EasyChartRE1NaturalBundle = EasyChartRE1FlowBundle
_runner.EasyChartRE1Strategy = EasyChartRE1FlowStrategy
_runner.add_symbol_mtf_data = add_symbol_mtf_flow_data


def _output_path(argv: list[str]) -> Path | None:
    try:
        return Path(argv[argv.index("--output") + 1])
    except (ValueError, IndexError):
        return None


def _rewrite_metadata(output: Path) -> None:
    flow_metadata = {
        "candidate": "candidate-easychart_re1_flow",
        "decision_policy": (
            "causal 60m/15m structure -> liquidity interaction -> existing OB/FVG or exact retest "
            "OR coherent taker initiative/boundary absorption -> immutable full-position plan"
        ),
        "flow_data_policy": (
            "EXACT_BINANCE_ONE_MINUTE_QUOTE_VOLUME_TRADE_COUNT_TAKER_BUY_BASE_AND_QUOTE_VOLUME"
        ),
        "flow_entry_policy": (
            "EXISTING_VISUAL_ENTRY_HAS_PRIORITY; FLOW_SUBSTITUTES_FOR_MISSING_OR_DELAYED_FOOTPRINT; "
            "CONFIRMED_ACCEPTED_BREAK_MAY_USE_FIRST_COHERENT_INITIATIVE"
        ),
        "flow_rule_provenance": [
            BINANCE_AGGRESSOR_FLOW_RULE,
            CAUSAL_FLOW_BASELINE_RULE,
            FLOW_SUBSTITUTION_RULE,
            FLOW_BREAKOUT_RULE,
        ],
    }
    for name in ("metrics.json", "run.json"):
        path = output / name
        if not path.exists():
            continue
        record = json.loads(path.read_text(encoding="utf-8"))
        record.update(flow_metadata)
        path.write_text(
            json.dumps(record, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )


if __name__ == "__main__":
    destination = _output_path(sys.argv)
    _runner.main()
    if destination is not None:
        _rewrite_metadata(destination)
