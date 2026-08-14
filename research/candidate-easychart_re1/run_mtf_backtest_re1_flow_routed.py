#!/usr/bin/env python3
"""Run the mechanism-routed causal aggressor-flow RE1 candidate."""
from __future__ import annotations

import json
from pathlib import Path
import sys

from easychart_re1_flow_routed import (
    FLOW_ABSORPTION_REVERSAL_RULE,
    FLOW_ROUTER_RESPONSIBILITY_RULE,
    EasyChartRE1FlowRoutedBundle,
)
import run_mtf_backtest_re1_flow as _flow_runner


_flow_runner._runner.EasyChartRE1NaturalBundle = EasyChartRE1FlowRoutedBundle


def _rewrite_route_metadata(output: Path) -> None:
    values = {
        "candidate": "candidate-easychart_re1_flow_routed",
        "flow_router_policy": (
            "ORDINARY_VISUAL_PLANS_KEEP_INHERITED_ROUTING; EXPLICIT_INITIATIVE_OR_ABSORPTION "
            "OWNS_THE_MISSING_FLOW_ENTRY_RESPONSIBILITY"
        ),
        "flow_router_rule_provenance": [
            FLOW_ROUTER_RESPONSIBILITY_RULE,
            FLOW_ABSORPTION_REVERSAL_RULE,
        ],
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
        _rewrite_route_metadata(destination)
