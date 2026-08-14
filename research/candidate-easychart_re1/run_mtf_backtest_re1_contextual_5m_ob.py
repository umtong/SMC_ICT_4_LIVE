#!/usr/bin/env python3
"""Run the contextual five-minute OB impulse-flow candidate."""
from __future__ import annotations

import json
from pathlib import Path
import sys

from easychart_re1_contextual_5m_ob import (
    CONTEXTUAL_FIVE_MINUTE_OB_FLOW_RULE,
    CONTEXTUAL_FIVE_MINUTE_OB_RULE,
    SAME_CLOSE_FIVE_MINUTE_FLOW_RULE,
    EasyChartRE1ContextualFiveMinuteOBBundle,
)
import run_mtf_backtest_re1_flow as _flow_runner


_flow_runner._runner.EasyChartRE1NaturalBundle = EasyChartRE1ContextualFiveMinuteOBBundle


def _rewrite_metadata(output: Path) -> None:
    values = {
        "candidate": "candidate-easychart_re1_contextual_5m_ob",
        "contextual_five_minute_ob_policy": (
            "60M_CONTEXT_ROUTER_PLUS_ORDERED_15M_STRUCTURE_PLUS_5M_ENGULFING_OB_PLUS_ALIGNED_1M_IMPULSE_FLOW"
        ),
        "contextual_five_minute_ob_rule_provenance": (
            CONTEXTUAL_FIVE_MINUTE_OB_RULE,
            CONTEXTUAL_FIVE_MINUTE_OB_FLOW_RULE,
            SAME_CLOSE_FIVE_MINUTE_FLOW_RULE,
        ),
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
    destination = _flow_runner._output_path(sys.argv)
    _flow_runner._runner.main()
    if destination is not None:
        _flow_runner._rewrite_metadata(destination)
        _rewrite_metadata(destination)
