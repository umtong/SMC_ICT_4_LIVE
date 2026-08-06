#!/usr/bin/env python3
"""Run candidate-04 V19 acceptance-impact controls in NautilusTrader only."""
from __future__ import annotations

import os
from typing import Any

import nt_backtest as base
import nt_liquidity_strategy as core
from nt_causal_risk_sizing import risk_sized_submit_bracket


VARIANTS = {
    "external_fast": (
        "nt_acceptance_impact_strategy:ExternalFastAcceptanceStrategy"
    ),
    "external_all": (
        "nt_acceptance_impact_strategy:ExternalAllAcceptanceStrategy"
    ),
    "composite_fast": (
        "nt_acceptance_impact_strategy:CompositeFastAcceptanceStrategy"
    ),
    "composite_all": (
        "nt_acceptance_impact_strategy:CompositeAllAcceptanceStrategy"
    ),
}


core.LiquidityTransitionStrategy._submit_bracket = risk_sized_submit_bracket
_original_importable_strategy_config = base.ImportableStrategyConfig


def _strategy_config(*args: Any, **kwargs: Any) -> Any:
    if kwargs.get("strategy_path") == "nt_liquidity_strategy:LiquidityTransitionStrategy":
        variant = os.environ.get("C04_NT_VARIANT", "external_fast")
        try:
            kwargs["strategy_path"] = VARIANTS[variant]
        except KeyError as exc:
            raise RuntimeError(
                f"unknown C04_NT_VARIANT={variant!r}; expected {sorted(VARIANTS)}",
            ) from exc
    return _original_importable_strategy_config(*args, **kwargs)


base.ImportableStrategyConfig = _strategy_config


if __name__ == "__main__":
    base.main()
