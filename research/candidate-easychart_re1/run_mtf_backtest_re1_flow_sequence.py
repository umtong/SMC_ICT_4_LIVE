#!/usr/bin/env python3
"""Run the liquidity-absorption-response sequence RE1 candidate."""
from __future__ import annotations

import json
from pathlib import Path
import sys

from easychart_re1_flow_sequence import (
    ACCEPTANCE_RETEST_FLOW_RULE,
    FLOW_SEQUENCE_RULE,
    EasyChartRE1SequenceFlowBundle,
)
import run_mtf_backtest_re1_flow as _flow_runner


_flow_runner._runner.EasyChartRE1NaturalBundle = EasyChartRE1SequenceFlowBundle


def _rewrite_sequence_metadata(output: Path) -> None:
    values = {
        "candidate": "candidate-easychart_re1_flow_sequence",
        "flow_sequence_policy": (
            "PREEXISTING_BOUNDARY -> OPPOSING_AGGRESSION_ABSORPTION -> RECLAIM_OR_FIRST_RESPONSE_INITIATIVE; "
            "ACCEPTED_FLIP_REQUIRES_ACTUAL_RETEST"
        ),
        "flow_sequence_rule_provenance": [
            FLOW_SEQUENCE_RULE,
            ACCEPTANCE_RETEST_FLOW_RULE,
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
        _rewrite_sequence_metadata(destination)
