#!/usr/bin/env python3
"""Run original flow-valid 15m OBs with single-entry evidence ownership."""
from __future__ import annotations

import json
from pathlib import Path
import sys

from easychart_re1_flow_ob_responsibility import EasyChartRE1ResponsibleFlowOBBundle
from easychart_re1_flow_ob_sweep_responsibility import SINGLE_ENTRY_EVIDENCE_OWNER_RULE
import run_mtf_backtest_re1_flow as _flow_runner


_flow_runner._runner.EasyChartRE1NaturalBundle = EasyChartRE1ResponsibleFlowOBBundle


def _rewrite_metadata(output: Path) -> None:
    values = {
        "candidate": "candidate-easychart_re1_flow_ob_responsibility",
        "policy": "ORDERED_PHASE_FLOW_CORE_PLUS_FLOW_VALIDATED_OB_WITH_SINGLE_ENTRY_OWNER",
        "entry_owner_rule": SINGLE_ENTRY_EVIDENCE_OWNER_RULE,
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
