#!/usr/bin/env python3
"""Run the decisive absorption rejection core under fast common-flow persistence."""
from __future__ import annotations

import json
from pathlib import Path
import sys

from easychart_re1_rejection_micro_target import (
    FIRST_UNSPENT_MICRO_FOOTPRINT_TARGET_RULE,
    REJECTION_ONLY_RESPONSIBILITY_RULE,
)
from easychart_re1_strong_absorption_target import (
    STRONG_ABSORPTION_TRANSFER_RULE,
    EasyChartRE1StrongAbsorptionTargetBundle,
)
from execution_re1_factor_persistence import (
    PERSISTENT_ALIGNED_ROUTING_RULE,
    PERSISTENT_COMMON_AUCTION_RULE,
    TRANSITIONAL_VISUAL_OWNERSHIP_RULE,
    TURBULENT_ABSTENTION_RULE,
    EasyChartRE1PersistentFactorStrategy,
)
import run_mtf_backtest_re1_flow as _flow_runner

_flow_runner._runner.EasyChartRE1NaturalBundle = EasyChartRE1StrongAbsorptionTargetBundle
_flow_runner._runner.EasyChartRE1Strategy = EasyChartRE1PersistentFactorStrategy


def _rewrite_metadata(output: Path) -> None:
    values = {
        "candidate": "candidate-easychart_re1_persistent_strong_absorption_target",
        "decision_policy": (
            "persistent common-flow routing plus rejection-only decisive absorption/visual first-return entries; "
            "full exit is the first pre-existing untouched high-quality opposing one-minute OB/FVG or nearer inherited structure"
        ),
        "entry_rules": [
            REJECTION_ONLY_RESPONSIBILITY_RULE,
            STRONG_ABSORPTION_TRANSFER_RULE,
        ],
        "objective_rule": FIRST_UNSPENT_MICRO_FOOTPRINT_TARGET_RULE,
        "factor_rules": [
            PERSISTENT_COMMON_AUCTION_RULE,
            PERSISTENT_ALIGNED_ROUTING_RULE,
            TURBULENT_ABSTENTION_RULE,
            TRANSITIONAL_VISUAL_OWNERSHIP_RULE,
        ],
    }
    for name in ("metrics.json", "run.json"):
        path = output / name
        if not path.exists():
            continue
        record = json.loads(path.read_text(encoding="utf-8"))
        record.update(values)
        path.write_text(json.dumps(record, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


if __name__ == "__main__":
    destination = _flow_runner._output_path(sys.argv)
    _flow_runner._runner.main()
    if destination is not None:
        _flow_runner._rewrite_metadata(destination)
        _rewrite_metadata(destination)
