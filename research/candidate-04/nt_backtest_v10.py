#!/usr/bin/env python3
"""Run candidate-04 opening-auction variants through NautilusTrader only."""
from __future__ import annotations

import os
from typing import Any

import nt_backtest as base


VARIANTS = {
    "opening30": "nt_opening_auction_strategy:OpeningAuction30Strategy",
    "opening60": "nt_opening_auction_strategy:OpeningAuction60Strategy",
}

_original_importable_strategy_config = base.ImportableStrategyConfig


def _strategy_config(*args: Any, **kwargs: Any) -> Any:
    if kwargs.get("strategy_path") == "nt_liquidity_strategy:LiquidityTransitionStrategy":
        variant = os.environ.get("C04_NT_VARIANT", "opening60")
        try:
            kwargs["strategy_path"] = VARIANTS[variant]
        except KeyError as exc:
            raise RuntimeError(
                f"unknown C04_NT_VARIANT={variant!r}; expected one of {sorted(VARIANTS)}",
            ) from exc
    return _original_importable_strategy_config(*args, **kwargs)


base.ImportableStrategyConfig = _strategy_config


if __name__ == "__main__":
    base.main()
