#!/usr/bin/env python3
"""Run the integrated auction policy in one continuous four-market account."""
from __future__ import annotations

import json
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
RESEARCH = HERE.parent
for candidate in (
    HERE,
    RESEARCH / "candidate-easychart_ml3v3",
    RESEARCH / "candidate-easychart_ml3v2",
    RESEARCH / "candidate-easychart_re1",
    RESEARCH / "candidate-easychart-v5",
    RESEARCH / "candidate-easychart-v3",
    RESEARCH / "candidate-easychart-v2",
):
    value = str(candidate)
    if value not in sys.path:
        sys.path.insert(0, value)

from execution_integrated import IntegratedAuctionExecutionStrategy  # noqa: E402
from integrated_auction import IntegratedAuctionBundle  # noqa: E402
import run_mtf_backtest_re1_flow as _flow_runner  # noqa: E402

_flow_runner._runner.EasyChartRE1NaturalBundle = IntegratedAuctionBundle
_flow_runner._runner.EasyChartRE1Strategy = IntegratedAuctionExecutionStrategy


def _rewrite(output: Path) -> None:
    values = {
        "candidate": "candidate-easychart_ml3_integrated",
        "decision_policy": (
            "HIERARCHICAL_DIRECTION_AND_CHANNEL -> LIQUIDITY_EVENT -> FIRST_MITIGATION -> "
            "NOISE_AWARE_INVALIDATION -> OPPOSING_DECISION_STRUCTURE"
        ),
        "position_policy": "ONE_GLOBAL_FULL_POSITION_NO_PARTIAL_NO_RATCHET",
        "risk_policy": "FIXED_APPROX_3_PERCENT_NAV_LOSS_AT_PRE_ENTRY_INVALIDATION",
    }
    for name in ("metrics.json", "run.json"):
        path = output / name
        if path.exists():
            record = json.loads(path.read_text(encoding="utf-8"))
            record.update(values)
            path.write_text(json.dumps(record, ensure_ascii=False, indent=2, sort_keys=True) + "\n")


def main() -> None:
    destination = _flow_runner._output_path(sys.argv)
    _flow_runner._runner.main()
    if destination is not None:
        _flow_runner._rewrite_metadata(destination)
        _rewrite(destination)


if __name__ == "__main__":
    main()
