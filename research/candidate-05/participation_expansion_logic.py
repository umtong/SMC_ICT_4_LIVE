"""Pure causal predicates for OI-expanding information repricing."""
from __future__ import annotations

import math


MIN_MOVE_ATR = 0.50
MIN_PATH_EFFICIENCY = 0.35
MIN_OI_EXPANSION_5M = 0.001
MIN_DIRECTIONAL_FLOW_3M = 0.20
MIN_SPOT_RETURN_BPS = 2.0
MAX_PERPETUAL_LEAD_OVER_SPOT_BPS = 0.50
MIN_PULLBACK_DEPTH = 0.10


def participation_expansion_direction(
    *,
    move_atr: float,
    path_efficiency: float,
    oi_change_5m: float,
    spot_flow_3m: float,
    perpetual_flow_3m: float,
    spot_return_bps: float,
    perp_minus_spot_return_bps: float,
) -> int:
    """Return direction when new positions sponsor efficient joint repricing."""
    values = (
        move_atr,
        path_efficiency,
        oi_change_5m,
        spot_flow_3m,
        perpetual_flow_3m,
        spot_return_bps,
        perp_minus_spot_return_bps,
    )
    if not all(math.isfinite(float(value)) for value in values):
        return 0
    move = float(move_atr)
    if abs(move) < MIN_MOVE_ATR:
        return 0
    direction = 1 if move > 0.0 else -1
    return direction if (
        float(path_efficiency) >= MIN_PATH_EFFICIENCY
        and float(oi_change_5m) >= MIN_OI_EXPANSION_5M
        and direction * float(spot_flow_3m) >= MIN_DIRECTIONAL_FLOW_3M
        and direction * float(perpetual_flow_3m) >= MIN_DIRECTIONAL_FLOW_3M
        and direction * float(spot_return_bps) >= MIN_SPOT_RETURN_BPS
        and direction * float(perp_minus_spot_return_bps)
        <= MAX_PERPETUAL_LEAD_OVER_SPOT_BPS
    ) else 0


def first_expansion_pullback_defended(
    *,
    side: int,
    midpoint: float,
    high: float,
    low: float,
    close: float,
    flow_15s: float,
    depth_imbalance: float,
    spot_flow_60s: float,
) -> bool:
    """Whether the first completed midpoint touch was defended by both auctions."""
    if side not in (-1, 1):
        raise ValueError("side must be -1 or 1")
    values = (
        midpoint,
        high,
        low,
        close,
        flow_15s,
        depth_imbalance,
        spot_flow_60s,
    )
    if not all(math.isfinite(float(value)) for value in values):
        return False
    if high < low:
        return False
    touched = float(low) <= float(midpoint) <= float(high)
    defended = float(close) > float(midpoint) if side > 0 else float(close) < float(midpoint)
    return (
        touched
        and defended
        and side * float(flow_15s) >= 0.0
        and side * float(depth_imbalance) >= MIN_PULLBACK_DEPTH
        and side * float(spot_flow_60s) >= 0.0
    )


__all__ = [
    "MAX_PERPETUAL_LEAD_OVER_SPOT_BPS",
    "MIN_DIRECTIONAL_FLOW_3M",
    "MIN_MOVE_ATR",
    "MIN_OI_EXPANSION_5M",
    "MIN_PATH_EFFICIENCY",
    "MIN_PULLBACK_DEPTH",
    "MIN_SPOT_RETURN_BPS",
    "first_expansion_pullback_defended",
    "participation_expansion_direction",
]
