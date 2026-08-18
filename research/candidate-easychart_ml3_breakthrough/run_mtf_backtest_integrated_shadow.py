#!/usr/bin/env python3
"""Harvest every complete hierarchical integrated-auction plan."""
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

from execution_ml1 import ML1RuntimeConfig, configure_ml1_runtime  # noqa: E402
from execution_integrated import IntegratedAuctionShadowStrategy  # noqa: E402
from integrated_auction import IntegratedAuctionBundle  # noqa: E402
import run_mtf_backtest_re1_flow as _flow_runner  # noqa: E402

_flow_runner._runner.EasyChartRE1NaturalBundle = IntegratedAuctionBundle
_flow_runner._runner.EasyChartRE1Strategy = IntegratedAuctionShadowStrategy


def _rewrite(output: Path) -> None:
    values = {
        "candidate": "candidate-easychart_ml3_integrated_shadow",
        "research_role": "HIERARCHICAL_DIRECTION_LIQUIDITY_STRUCTURE_EVENT_ENTRY_HARVEST",
        "prior_policy_role": "NONE",
        "selector": "NONSELECTIVE_SHADOW",
        "decision_policy": (
            "PRICE_VOLUME -> 60M_15M_DIRECTION_AND_CHANNEL -> ACTIVE_LIQUIDITY -> "
            "SWEEP_RECLAIM_OR_ACCEPTED_BREAK -> FIRST_FVG_OB_RETEST -> "
            "NOISE_AWARE_INVALIDATION -> FIRST_DECISION_SCALE_OPPOSING_LIQUIDITY"
        ),
    }
    for name in ("metrics.json", "run.json"):
        path = output / name
        if not path.exists():
            continue
        record = json.loads(path.read_text(encoding="utf-8"))
        record.update(values)
        path.write_text(json.dumps(record, ensure_ascii=False, indent=2, sort_keys=True) + "\n")


def main() -> None:
    bootstrap = RESEARCH / "candidate-easychart_ml3v2" / "models" / "bootstrap_shadow.json"
    configure_ml1_runtime(ML1RuntimeConfig(mode="shadow", model_path=bootstrap))
    destination = _flow_runner._output_path(sys.argv)
    _flow_runner._runner.main()
    if destination is not None:
        _flow_runner._rewrite_metadata(destination)
        _rewrite(destination)


if __name__ == "__main__":
    main()
