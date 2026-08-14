#!/usr/bin/env python3
"""Run the independent local five-minute liquidity absorption family."""
from __future__ import annotations

import json
from pathlib import Path
import sys

from easychart_re1_flow_local_liquidity import (
    LOCAL_LIQUIDITY_ALIGNMENT_RULE,
    LOCAL_LIQUIDITY_FLOW_RULE,
    LOCAL_LIQUIDITY_TARGET_RULE,
    EasyChartRE1LocalLiquidityFlowBundle,
)
import run_mtf_backtest_re1_flow as _flow_runner


_flow_runner._runner.EasyChartRE1NaturalBundle = EasyChartRE1LocalLiquidityFlowBundle


def _rewrite_local_metadata(output: Path) -> None:
    values = {
        "candidate": "candidate-easychart_re1_flow_local_liquidity",
        "decision_policy": (
            "confirmed 5m swing first interaction -> first typical-volume cumulative opposing aggression "
            "absorbed and reclaimed -> 15m-aligned immutable plan"
        ),
        "executable_families": ["LOCAL_5M_SWEEP_ABSORPTION"],
        "local_liquidity_rule_provenance": [
            LOCAL_LIQUIDITY_FLOW_RULE,
            LOCAL_LIQUIDITY_TARGET_RULE,
            LOCAL_LIQUIDITY_ALIGNMENT_RULE,
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
        _rewrite_local_metadata(destination)
