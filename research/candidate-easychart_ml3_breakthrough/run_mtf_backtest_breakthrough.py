#!/usr/bin/env python3
"""Run the independent intrinsic-auction policy in one Nautilus account."""
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
    text = str(candidate)
    if text not in sys.path:
        sys.path.insert(0, text)

from execution_breakthrough import IntrinsicAuctionExecutionStrategy  # noqa: E402
from intrinsic_auction import IntrinsicAuctionBundle  # noqa: E402
import run_mtf_backtest_re1_flow as _flow_runner  # noqa: E402

_flow_runner._runner.EasyChartRE1NaturalBundle = IntrinsicAuctionBundle
_flow_runner._runner.EasyChartRE1Strategy = IntrinsicAuctionExecutionStrategy


def _rewrite(output: Path) -> None:
    values = {
        "candidate": "candidate-easychart_ml3_breakthrough_intrinsic_auction",
        "decision_policy": (
            "INTRINSIC_PUBLIC_LIQUIDITY -> SWEEP_RECLAIM_OR_ACCEPTED_BREAK -> "
            "FLOW_OR_PRICE_CONTROL_TRANSFER -> FIRST_MITIGATION_RESPONSE -> "
            "PREEXISTING_OPPOSING_LIQUIDITY"
        ),
        "prior_policy_role": "NONE",
        "position_policy": "ONE_GLOBAL_FULL_POSITION_NO_PARTIAL_NO_RATCHET",
        "risk_policy": "FIXED_APPROX_3_PERCENT_NAV_LOSS_AT_PRE_ENTRY_INVALIDATION",
    }
    for name in ("metrics.json", "run.json"):
        path = output / name
        if not path.exists():
            continue
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
