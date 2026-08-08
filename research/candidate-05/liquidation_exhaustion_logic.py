"""Pure causal predicates for perpetual-led liquidation exhaustion."""
from __future__ import annotations

import math


MIN_PERPETUAL_MOVE_ATR = 0.50
MIN_PERPETUAL_LEAD_BPS = 1.0
TAIL_IMPROVEMENT_MIN = 0.50
DIRECTIONAL_DEPTH_MIN = 0.20


def liquidation_exhaustion_side(
    *,
    perpetual_move_atr: float,
    perp_minus_spot_return_bps: float,
    oi_change_5m: float,
    spot_flow_3m: float,
) -> int:
    """Return the side opposite a perpetual-led, OI-contracting impulse."""
    values = (
        perpetual_move_atr,
        perp_minus_spot_return_bps,
        oi_change_5m,
        spot_flow_3m,
    )
    if not all(math.isfinite(float(value)) for value in values):
        return 0
    move = float(perpetual_move_atr)
    if abs(move) < MIN_PERPETUAL_MOVE_ATR or float(oi_change_5m) >= 0.0:
        return 0
    direction = 1 if move > 0.0 else -1
    if direction * float(perp_minus_spot_return_bps) < MIN_PERPETUAL_LEAD_BPS:
        return 0
    side = -direction
    # Spot must not be sponsoring the perpetual impulse; completed three-minute
    # spot aggressor flow must instead be neutral or point toward normalization.
    if side * float(spot_flow_3m) < 0.0:
        return 0
    return side


def liquidation_tail_reversal_confirmed(
    *,
    side: int,
    flow_15s: float,
    flow_60s: float,
    depth_imbalance: float,
) -> bool:
    if side not in (-1, 1):
        raise ValueError("side must be -1 or 1")
    values = (flow_15s, flow_60s, depth_imbalance)
    if not all(math.isfinite(float(value)) for value in values):
        return False
    return (
        side * (float(flow_15s) - float(flow_60s)) >= TAIL_IMPROVEMENT_MIN
        and side * float(depth_imbalance) >= DIRECTIONAL_DEPTH_MIN
    )


__all__ = [
    "DIRECTIONAL_DEPTH_MIN",
    "MIN_PERPETUAL_LEAD_BPS",
    "MIN_PERPETUAL_MOVE_ATR",
    "TAIL_IMPROVEMENT_MIN",
    "liquidation_exhaustion_side",
    "liquidation_tail_reversal_confirmed",
]
