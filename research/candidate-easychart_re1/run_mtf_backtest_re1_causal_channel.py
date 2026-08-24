#!/usr/bin/env python3
"""Run the canonical causal-channel policy in one continuous account."""
from __future__ import annotations

import json
import sys

from easychart_re1_causal_channel import (
    CAUSAL_CHANNEL_REVERSAL_SEQUENCE_RULE,
    EasyChartRE1CausalChannelBundle,
)
import run_mtf_backtest_re1_flow as _flow


_flow._runner.EasyChartRE1NaturalBundle = EasyChartRE1CausalChannelBundle


if __name__ == "__main__":
    output = _flow._output_path(sys.argv)
    _flow._runner.main()
    if output:
        _flow._rewrite_metadata(output)
        for name in ("metrics.json", "run.json"):
            path = output / name
            if path.exists():
                payload = json.loads(path.read_text(encoding="utf-8"))
                payload.update(
                    {
                        "candidate": "candidate-easychart_re1_causal_channel",
                        "rule": CAUSAL_CHANNEL_REVERSAL_SEQUENCE_RULE,
                    },
                )
                path.write_text(
                    json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                    encoding="utf-8",
                )
