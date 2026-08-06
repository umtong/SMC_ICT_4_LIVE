#!/usr/bin/env python3
"""Run candidate-04 causal-retest v4 through NautilusTrader BacktestNode."""
from __future__ import annotations

from typing import Any

import nt_backtest as base


_original_importable_strategy_config = base.ImportableStrategyConfig


def _strategy_config(*args: Any, **kwargs: Any) -> Any:
    if kwargs.get("strategy_path") == "nt_liquidity_strategy:LiquidityTransitionStrategy":
        kwargs["strategy_path"] = (
            "nt_liquidity_strategy_v4:LiquidityTransitionStrategyV4"
        )
    return _original_importable_strategy_config(*args, **kwargs)


base.ImportableStrategyConfig = _strategy_config


if __name__ == "__main__":
    base.main()
