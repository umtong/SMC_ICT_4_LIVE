#!/usr/bin/env python3
"""Run the complete EasyChart bot with persistent unified context."""
from __future__ import annotations

import json
from pathlib import Path
import sys

from easychart_re1_complete_bot_policy_v2 import UNIFIED_CONTINUATION_CONTEXT_RULE
from easychart_re1_complete_bot_policy_v3 import (
    PERSISTENT_UNIFIED_CONTEXT_RULE,
    EasyChartRE1CompleteBotPolicyV3Bundle,
    EasyChartRE1CompletePersistentStrategy,
)
import run_mtf_backtest_re1_flow as _flow_runner


_flow_runner._runner.EasyChartRE1NaturalBundle = EasyChartRE1CompleteBotPolicyV3Bundle
_flow_runner._runner.EasyChartRE1Strategy = EasyChartRE1CompletePersistentStrategy


def _rewrite_metadata(output: Path) -> None:
    values = {
        "candidate": "candidate-easychart_re1_complete_bot_policy_v3",
        "policy": (
            "COMPLETE_OPPORTUNITY_SET_WITH_UNIFIED_FAST_OR_PERSISTENT_COMMON_CONTEXT"
        ),
        "unified_context_rule": UNIFIED_CONTINUATION_CONTEXT_RULE,
        "persistent_context_rule": PERSISTENT_UNIFIED_CONTEXT_RULE,
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
