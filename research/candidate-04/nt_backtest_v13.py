#!/usr/bin/env python3
"""Run candidate-04 V13 variants through NautilusTrader BacktestNode only."""
from __future__ import annotations

import os
from typing import Any

import nt_backtest as base


VARIANTS = {
    "balance3": "nt_acceptance_balance_strategy:AcceptanceBalance3Strategy",
    "balance5": "nt_acceptance_balance_strategy:AcceptanceBalance5Strategy",
    "balance8": "nt_acceptance_balance_strategy:AcceptanceBalance8Strategy",
}

_original_importable_strategy_config = base.ImportableStrategyConfig


def _strategy_config(*args: Any, **kwargs: Any) -> Any:
    if kwargs.get("strategy_path") == "nt_liquidity_strategy:LiquidityTransitionStrategy":
        variant = os.environ.get("C04_NT_VARIANT", "balance5")
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
