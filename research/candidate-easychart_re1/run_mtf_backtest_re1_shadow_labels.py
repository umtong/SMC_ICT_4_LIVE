#!/usr/bin/env python3
"""Run integrated state policy while labeling every emitted plan counterfactually."""
from __future__ import annotations

import json
from pathlib import Path
import sys

from easychart_re1_state_policy import EasyChartRE1StatePolicyBundle
from execution_re1_shadow_labels import (
    COUNTERFACTUAL_FIXED_PLAN_LABEL_RULE,
    EasyChartRE1ShadowLabelStrategy,
)
import run_mtf_backtest_re1_flow as _flow_runner

_flow_runner._runner.EasyChartRE1NaturalBundle = EasyChartRE1StatePolicyBundle
_flow_runner._runner.EasyChartRE1Strategy = EasyChartRE1ShadowLabelStrategy


def _rewrite_metadata(output: Path) -> None:
    values = {
        "candidate": "candidate-easychart_re1_shadow_labels",
        "decision_policy": "INTEGRATED_STATE_POLICY_WITH_NONTRADING_COUNTERFACTUAL_LABELS_FOR_ALL_EMITTED_PLANS",
        "shadow_label_rule": COUNTERFACTUAL_FIXED_PLAN_LABEL_RULE,
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
