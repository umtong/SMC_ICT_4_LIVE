#!/usr/bin/env python3
"""Run the latent liquidity-episode policy in one continuous account."""
from __future__ import annotations

import json
import sys

from easychart_re1_bot import EasyChartRE1BotBundle, EasyChartRE1BotStrategy
import run_mtf_backtest_re1_flow as _flow


_flow._runner.EasyChartRE1NaturalBundle = EasyChartRE1BotBundle
_flow._runner.EasyChartRE1Strategy = EasyChartRE1BotStrategy

if __name__ == "__main__":
    output = _flow._output_path(sys.argv)
    _flow._runner.main()
    if output is not None:
        _flow._rewrite_metadata(output)
        for name in ("metrics.json", "run.json"):
            path = output / name
            if not path.exists():
                continue
            payload = json.loads(path.read_text(encoding="utf-8"))
            payload.update(
                {
                    "candidate": "candidate-latent-liquidity-episode-policy-v1",
                    "policy": (
                        "ACTIVE_LIQUIDITY_DRAW_AND_HIERARCHICAL_CONTEXT_TO_"
                        "SINGLE_FAILED_OR_ACCEPTED_AUCTION_OWNER_TO_FIRST_"
                        "RETURN_RESPONSE_TO_FIRST_UNSPENT_OPPOSING_LIQUIDITY"
                    ),
                },
            )
            path.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
                + "\n",
                encoding="utf-8",
            )
