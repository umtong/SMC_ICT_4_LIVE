#!/usr/bin/env python3
"""Run the embedded accepted-break candidate in one continuous account."""
from __future__ import annotations

import json
from pathlib import Path
import sys

from easychart_re1_embedded_acceptance import (
    EMBEDDED_ACCEPTANCE_RETEST_RULE,
    SAME_TIMESTAMP_COMPLETED_ENTRY_RULE,
    EasyChartRE1EmbeddedAcceptanceBundle,
)
import run_mtf_backtest_re1_flow as flow_runner


flow_runner._runner.EasyChartRE1NaturalBundle = EasyChartRE1EmbeddedAcceptanceBundle


def rewrite_metadata(output: Path) -> None:
    values = {
        "candidate": "candidate-easychart_re1_embedded_acceptance",
        "policy": "RESPONSIBLE_FLOW_OB_PLUS_EMBEDDED_ACCEPTANCE_RETEST",
        "embedded_acceptance_rules": [
            EMBEDDED_ACCEPTANCE_RETEST_RULE,
            SAME_TIMESTAMP_COMPLETED_ENTRY_RULE,
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
    destination = flow_runner._output_path(sys.argv)
    flow_runner._runner.main()
    if destination is not None:
        flow_runner._rewrite_metadata(destination)
        rewrite_metadata(destination)
