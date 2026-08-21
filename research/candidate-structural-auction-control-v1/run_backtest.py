#!/usr/bin/env python3
"""Run structural auction control v1 in the continuous Nautilus account."""
from __future__ import annotations

import json
from pathlib import Path
import sys

import run_mtf_backtest_re1_flow as _flow
from structural_auction_control_v1 import (
    EFFORT_RESULT_RULE,
    FAILED_ACCEPTANCE_TRAP_RULE,
    PRICE_VOLUME_CONTROL_RULE,
    SINGLE_EPISODE_STATE_RULE,
    StructuralAuctionControlV1Bundle,
)


# Keep the existing Binance extended-kline loader, Nautilus execution strategy,
# fees, slippage, one-global-position account, and 3% risk sizing. Only the
# decision bundle is replaced.
_flow._runner.EasyChartRE1NaturalBundle = StructuralAuctionControlV1Bundle


def _rewrite(output: Path) -> None:
    metadata = {
        "candidate": "candidate-structural-auction-control-v1",
        "decision_policy": (
            "60m direction and reversal-area context -> causal 15m public structure -> "
            "5m rejection/acceptance/trap state ownership -> completed 1m aggressor-flow "
            "initiative or absorption -> first event-local OB/FVG or exact retest -> "
            "immutable full-position structural plan"
        ),
        "structural_changes": [
            "FAILED_ACCEPTANCE_TRANSITIONS_TO_SAME_EPISODE_TRAP",
            "PRICE_AND_VOLUME_CONTROL_REQUIRED_BEFORE_PLAN",
            "NO_INDEPENDENT_OB_FVG_CHANNEL_OR_TRAP_STRATEGY_LATTICE",
        ],
        "rule_provenance": [
            FAILED_ACCEPTANCE_TRAP_RULE,
            PRICE_VOLUME_CONTROL_RULE,
            EFFORT_RESULT_RULE,
            SINGLE_EPISODE_STATE_RULE,
        ],
    }
    _flow._rewrite_metadata(output)
    for name in ("metrics.json", "run.json"):
        path = output / name
        if not path.exists():
            continue
        record = json.loads(path.read_text(encoding="utf-8"))
        record.update(metadata)
        path.write_text(
            json.dumps(record, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )


if __name__ == "__main__":
    destination = _flow._output_path(sys.argv)
    _flow._runner.main()
    if destination is not None:
        _rewrite(destination)
