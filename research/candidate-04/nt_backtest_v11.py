#!/usr/bin/env python3
"""Run candidate-04 V11 through NautilusTrader BacktestNode only."""
from __future__ import annotations

from typing import Any

import nt_backtest as base


_original_importable_strategy_config = base.ImportableStrategyConfig


def _strategy_config(*args: Any, **kwargs: Any) -> Any:
    if kwargs.get("strategy_path") == "nt_liquidity_strategy:LiquidityTransitionStrategy":
        kwargs["strategy_path"] = (
            "nt_dual_risk_auction_strategy:DualRiskAuctionStrategy"
        )
    return _original_importable_strategy_config(*args, **kwargs)


base.ImportableStrategyConfig = _strategy_config


if __name__ == "__main__":
    base.main()
