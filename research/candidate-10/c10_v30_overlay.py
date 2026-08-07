"""v30 source-equilibrium risk transfer over v29/v28/v27.

FAR begins as a failed external auction.  Once price reaches the midpoint of the
already-completed source dealing range, the market has delivered to equilibrium.
The residual external-draw runner may continue, but the original raid risk is no
longer logically appropriate.  The stop is therefore transferred to a
cost-neutral price.  This is a causal auction-state transition, not a fixed-R
trailing rule.
"""
from __future__ import annotations

from decimal import Decimal
import os
from typing import Any

from c10_v29_overlay import (  # re-export the frozen lower layers
    CostAwareRiskSizer,
    LiveImpactLedger,
    apply_cost_overlay,
    build_leadership_gate,
    certify_plan,
    repair_kline_flow_frame,
)


def far_only_enabled() -> bool:
    return os.environ.get("C10_V30_FAR_ONLY", "0") == "1"


def equilibrium_enabled() -> bool:
    return os.environ.get("C10_V30_EQUILIBRIUM", "0") == "1"


def equilibrium_reached(
    *,
    direction: str,
    high: float,
    low: float,
    midpoint: float,
) -> bool:
    if direction == "LONG":
        return high >= midpoint
    if direction == "SHORT":
        return low <= midpoint
    raise ValueError(f"unsupported direction: {direction}")


def cost_neutral_stop(
    *,
    direction: str,
    entry_price: float,
    maker_fee: float,
    taker_fee: float,
    impact_per_side: float,
) -> float:
    """Return the stop whose modeled all-cost PnL is zero per unit."""
    values = (entry_price, maker_fee, taker_fee, impact_per_side)
    if entry_price <= 0.0 or any(value < 0.0 for value in values[1:]):
        raise ValueError("invalid cost-neutral stop inputs")
    if direction == "LONG":
        denominator = 1.0 - taker_fee
        if denominator <= 0.0:
            raise ValueError("long taker fee leaves no positive denominator")
        return (entry_price * (1.0 + maker_fee) + 2.0 * impact_per_side) / denominator
    if direction == "SHORT":
        denominator = 1.0 + taker_fee
        numerator = entry_price * (1.0 - maker_fee) - 2.0 * impact_per_side
        if numerator <= 0.0:
            raise ValueError("short costs exceed entry price")
        return numerator / denominator
    raise ValueError(f"unsupported direction: {direction}")


def source_midpoint(logic: Any, scenario_id: str) -> float | None:
    pool = next(
        (item for item in logic.pools if item.scenario_id == scenario_id),
        None,
    )
    if pool is None or pool.opposite_level is None:
        return None
    return (float(pool.level) + float(pool.opposite_level)) / 2.0
