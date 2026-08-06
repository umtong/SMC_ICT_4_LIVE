#!/usr/bin/env python3
"""Run the corrected candidate-04 strategy through the unchanged NT runner."""
from __future__ import annotations

from typing import Any

import nt_backtest as base


_original_importable_strategy_config = base.ImportableStrategyConfig


def _corrected_strategy_config(*args: Any, **kwargs: Any) -> Any:
    if kwargs.get("strategy_path") == "nt_liquidity_strategy:LiquidityTransitionStrategy":
        kwargs["strategy_path"] = (
            "nt_liquidity_strategy_v2:LiquidityTransitionStrategyV2"
        )
    return _original_importable_strategy_config(*args, **kwargs)


base.ImportableStrategyConfig = _corrected_strategy_config


if __name__ == "__main__":
    base.main()
