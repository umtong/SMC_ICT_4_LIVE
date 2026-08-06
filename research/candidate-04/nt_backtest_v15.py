#!/usr/bin/env python3
"""Run candidate-04 V15 variants through NautilusTrader BacktestNode only."""
from __future__ import annotations

import os
from typing import Any

import nt_backtest as base


VARIANTS = {
    "internal15_strict": "nt_multiscale_liquidity_strategy:Internal15StrictStrategy",
    "internal15_near": "nt_multiscale_liquidity_strategy:Internal15NearEqualStrategy",
    "external60_strict": "nt_multiscale_liquidity_strategy:External60StrictStrategy",
    "external60_near": "nt_multiscale_liquidity_strategy:External60NearEqualStrategy",
}

_original_importable_strategy_config = base.ImportableStrategyConfig


def _strategy_config(*args: Any, **kwargs: Any) -> Any:
    if kwargs.get("strategy_path") == "nt_liquidity_strategy:LiquidityTransitionStrategy":
        variant = os.environ.get("C04_NT_VARIANT", "internal15_strict")
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
