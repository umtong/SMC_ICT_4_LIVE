#!/usr/bin/env python3
"""Run V56 signals through the trusted NautilusTrader single-asset contract."""
from __future__ import annotations

import os
from typing import Any

import nt_backtest as base
import nt_liquidity_strategy as core
from nt_conditional_adverse_fill_risk_sizing import (
    conditional_adverse_fill_submit_bracket,
)


core.LiquidityTransitionStrategy._submit_bracket = (
    conditional_adverse_fill_submit_bracket
)
_original_importable_strategy_config = base.ImportableStrategyConfig


def _strategy_config(*args: Any, **kwargs: Any) -> Any:
    if kwargs.get("strategy_path") == "nt_liquidity_strategy:LiquidityTransitionStrategy":
        signals_path = os.environ.get("C04_SIGNALS_PATH")
        if not signals_path:
            raise RuntimeError("C04_SIGNALS_PATH is required")
        config = dict(kwargs.get("config", {}))
        config["signals_path"] = signals_path
        kwargs["strategy_path"] = (
            "nt_state_preserving_rich_signal_strategy:"
            "StatePreservingRichSignalStrategy"
        )
        kwargs["config_path"] = "nt_rich_signal_strategy:RichSignalConfig"
        kwargs["config"] = config
    return _original_importable_strategy_config(*args, **kwargs)


base.ImportableStrategyConfig = _strategy_config


if __name__ == "__main__":
    base.main()
