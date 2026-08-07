#!/usr/bin/env python3
"""Run candidate-02 v105 exclusively through NautilusTrader 1.230.0."""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import v53_nt_backtest as runner
from v105_auction_state_core import AuctionStateConfig, build_scenario_result, build_state
from v105_nt_strategy import V105AuctionStateStrategy


def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(item) for item in value]
    return str(value)


def _build_rotation_signals(**kwargs):
    result = build_scenario_result(**kwargs)
    destination = os.environ.get("V105_SIGNAL_DIAGNOSTICS")
    if destination:
        path = Path(destination)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                _json_safe({
                    "diagnostics": result.diagnostics,
                    "level_counts": result.level_counts,
                    "scheduled_signals": len(result.signals),
                }),
                indent=2,
                sort_keys=True,
            ) + "\n",
            encoding="utf-8",
        )
    return list(result.signals)


runner.RotationConfig = AuctionStateConfig
runner.build_state = build_state
runner.build_rotation_signals = _build_rotation_signals
runner.V53RotationStrategy = V105AuctionStateStrategy

if __name__ == "__main__":
    raise SystemExit(runner.main())
