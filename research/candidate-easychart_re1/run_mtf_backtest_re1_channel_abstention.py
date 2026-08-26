#!/usr/bin/env python3
"""Run the channel-reversal abstention ablation in one continuous account."""
from __future__ import annotations

import json
from pathlib import Path
import sys

from easychart_re1_channel_abstention import (
    CHANNEL_REVERSAL_ABSTENTION_RULE,
    EasyChartRE1ChannelAbstentionBundle,
)
import run_mtf_backtest_re1_flow as _flow_runner


_flow_runner._runner.EasyChartRE1NaturalBundle = EasyChartRE1ChannelAbstentionBundle


def _rewrite_metadata(output: Path) -> None:
    values = {
        "candidate": "candidate-easychart_re1_channel_abstention",
        "decision_policy": (
            "reversal-only core with channel-edge reversals diagnostic-only; "
            "standalone trend lines, horizontal sweeps, major swings and original "
            "flow-valid 15m OB family remain executable"
        ),
        "channel_abstention_rule": CHANNEL_REVERSAL_ABSTENTION_RULE,
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
