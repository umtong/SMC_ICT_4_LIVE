#!/usr/bin/env python3
"""Four-instrument execution with repaired fill risk and evaluation bounds.

The V3 runner owns the one-account global coordinator, exact trusted
NautilusTrader venue configuration and risk-evidence reconciliation. This
wrapper changes only two implementation contracts already used by the trusted
single-instrument runner:

1. bracket sizing uses the conditional mean of completed adverse entry-delay
   transitions established by the V45c repair;
2. every imported strategy receives the declared evaluation start/end
   nanoseconds required by ``LiquidityTransitionConfig``.

No market-state, entry, target, stop, fee or execution-model logic is changed.
"""
from __future__ import annotations

from datetime import date, timedelta
import sys
from typing import Any

import pandas as pd

import nt_multi_asset_rich_backtest as runner
import nt_multi_asset_rich_backtest_v3 as base
import nt_liquidity_strategy as core
from nt_conditional_adverse_fill_risk_sizing import (
    conditional_adverse_fill_submit_bracket,
)


core.LiquidityTransitionStrategy._submit_bracket = (
    conditional_adverse_fill_submit_bracket
)

_ORIGINAL_STRATEGY_CONFIG = runner.strategy_config


def _argument_value(name: str, argv: list[str] | None = None) -> str | None:
    values = list(sys.argv if argv is None else argv)
    for index, value in enumerate(values[:-1]):
        if value == name:
            return values[index + 1]
    prefix = name + "="
    for value in values[1:]:
        if value.startswith(prefix):
            return value[len(prefix) :]
    return None


def evaluation_bounds_ns(argv: list[str] | None = None) -> tuple[int, int]:
    start_text = _argument_value("--evaluation-start", argv)
    end_text = _argument_value("--evaluation-end", argv)
    if start_text is None or end_text is None:
        raise RuntimeError("--evaluation-start and --evaluation-end are required")
    start = date.fromisoformat(start_text)
    end = date.fromisoformat(end_text)
    if end < start:
        raise RuntimeError("evaluation end precedes evaluation start")
    start_ts = pd.Timestamp(start, tz="UTC")
    end_ts = (
        pd.Timestamp(end + timedelta(days=1), tz="UTC")
        - pd.Timedelta(nanoseconds=1)
    )
    return int(start_ts.value), int(end_ts.value)


def _strategy_config_with_evaluation_bounds(
    symbol: str,
    config: dict[str, Any],
    signals_root: Any,
    strategy_root: Any,
    coordinator_key: str,
):
    start_ns, end_ns = evaluation_bounds_ns()
    values = dict(config)
    values["evaluation_start_ns"] = start_ns
    values["evaluation_end_ns"] = end_ns
    return _ORIGINAL_STRATEGY_CONFIG(
        symbol,
        values,
        signals_root,
        strategy_root,
        coordinator_key,
    )


runner.strategy_config = _strategy_config_with_evaluation_bounds


if __name__ == "__main__":
    base.main()
