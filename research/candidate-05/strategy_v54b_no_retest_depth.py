#!/usr/bin/env python3
"""Candidate 05 v54b: remove only displayed depth at the first retest.

The v54 acceptance event still requires withdrawal of liquidity ahead.  This
single-variable ablation asks whether the first retest's completed price defense
and final-15-second aggressor flow are sufficient when the displayed book points
the other way.  Every other v54 and inherited v46 contract is unchanged.
"""
from __future__ import annotations

import strategy_v54_failed_inventory_acceptance as _v54
from external_acceptance_retest_logic import (
    first_accepted_level_retest_response as _original_retest_response,
)


def _price_flow_only_first_retest_response(
    *,
    side: int,
    level: float,
    high: float,
    low: float,
    close: float,
    flow_15s: float,
    depth_imbalance: float,
    maximum_counterflow: float,
    minimum_directional_depth: float = 0.0,
) -> bool:
    del depth_imbalance, minimum_directional_depth
    return _original_retest_response(
        side=side,
        level=level,
        high=high,
        low=low,
        close=close,
        flow_15s=flow_15s,
        depth_imbalance=0.0,
        maximum_counterflow=maximum_counterflow,
        minimum_directional_depth=0.0,
    )


# The parent looks up this module global at runtime. Replacing only the retest
# predicate makes the ablation explicit without copying the strategy state
# machine or altering execution, target, stop, fees, sizing or lifecycle.
_v54.first_accepted_level_retest_response = _price_flow_only_first_retest_response


class FailedInventoryAcceptanceNoRetestDepthStrategy(
    _v54.FailedInventoryAcceptanceStrategy,
):
    pass


LiquidityResponseStrategy = FailedInventoryAcceptanceNoRetestDepthStrategy

__all__ = [
    "FailedInventoryAcceptanceNoRetestDepthStrategy",
    "LiquidityResponseStrategy",
]
