"""Pure causal predicates for spot-led perpetual repricing.

This module classifies only completed observations. It contains no order,
position, margin, fill, PnL, or NAV logic. The intended economic distinction is
between a perpetual-only inventory shock and information first expressed in the
spot auction, then accepted by the perpetual market.
"""
from __future__ import annotations

import math


TWO_TO_ONE_IMBALANCE = 1.0 / 3.0
SPOT_FLOW_15S_MIN = TWO_TO_ONE_IMBALANCE
SPOT_FLOW_60S_MIN = 0.20
SPOT_NOTIONAL_BURST_MIN = 1.50
SPOT_EFFICIENCY_MIN = 0.20
SPOT_PRICE_MOVE_BPS_MIN = 2.0
SPOT_LEAD_BPS_MIN = 1.0

SPOT_CONTEXT_ACCEPTANCE_DISTANCE_ATR = 0.50
SPOT_CONTEXT_INVALIDATION_ATR = 0.20
SPOT_CONTEXT_ACCEPTANCE_FLOW_3M_MIN = 0.0
SPOT_CONTEXT_MIN_AGE_BARS = 15
SPOT_CONTEXT_MAX_AGE_BARS = 75


def _finite(*values: float) -> bool:
    return all(math.isfinite(float(value)) for value in values)


def spot_led_repricing_direction(
    *,
    spot_flow_15s: float,
    spot_flow_60s: float,
    spot_notional_burst: float,
    spot_return_bps: float,
    spot_efficiency: float,
    perpetual_return_bps: float,
) -> int:
    """Return +1/-1 only when completed spot activity led the same-minute move.

    Direction is taken from the short spot aggressor flow. Both spot flow
    horizons must agree, the move must be active and efficient, and spot's
    directional return must exceed the perpetual return by a non-trivial amount.
    This deliberately rejects a perpetual-led liquidation impulse that happens
    to drag spot along afterwards.
    """
    values = (
        spot_flow_15s,
        spot_flow_60s,
        spot_notional_burst,
        spot_return_bps,
        spot_efficiency,
        perpetual_return_bps,
    )
    if not _finite(*values):
        return 0
    if abs(float(spot_flow_15s)) < SPOT_FLOW_15S_MIN:
        return 0
    direction = 1 if spot_flow_15s > 0.0 else -1
    directional_lead = direction * (
        float(spot_return_bps) - float(perpetual_return_bps)
    )
    return direction if (
        direction * float(spot_flow_60s) >= SPOT_FLOW_60S_MIN
        and float(spot_notional_burst) >= SPOT_NOTIONAL_BURST_MIN
        and direction * float(spot_return_bps) >= SPOT_PRICE_MOVE_BPS_MIN
        and float(spot_efficiency) >= SPOT_EFFICIENCY_MIN
        and directional_lead >= SPOT_LEAD_BPS_MIN
    ) else 0


def spot_context_invalidated(
    *,
    direction: int,
    boundary_low: float,
    boundary_high: float,
    current_close: float,
    atr: float,
) -> bool:
    if direction not in (-1, 1):
        raise ValueError("direction must be -1 or 1")
    if not _finite(boundary_low, boundary_high, current_close, atr) or atr <= 0.0:
        return True
    if direction > 0:
        return current_close < boundary_low - SPOT_CONTEXT_INVALIDATION_ATR * atr
    return current_close > boundary_high + SPOT_CONTEXT_INVALIDATION_ATR * atr


def spot_context_accepted(
    *,
    direction: int,
    boundary_close: float,
    favorable_extreme: float,
    atr: float,
    perpetual_flow_3m: float,
) -> bool:
    """Whether perpetual price and broad aggressor flow accepted the spot lead."""
    if direction not in (-1, 1):
        raise ValueError("direction must be -1 or 1")
    if not _finite(
        boundary_close,
        favorable_extreme,
        atr,
        perpetual_flow_3m,
    ) or atr <= 0.0:
        return False
    displacement = direction * (
        float(favorable_extreme) - float(boundary_close)
    ) / float(atr)
    return (
        displacement >= SPOT_CONTEXT_ACCEPTANCE_DISTANCE_ATR
        and direction * float(perpetual_flow_3m)
        >= SPOT_CONTEXT_ACCEPTANCE_FLOW_3M_MIN
    )


def spot_context_entry_eligible(
    *,
    setup_side: int,
    context_direction: int,
    context_age_bars: int,
    context_accepted: bool,
) -> bool:
    return (
        setup_side in (-1, 1)
        and context_direction in (-1, 1)
        and setup_side == context_direction
        and context_accepted
        and SPOT_CONTEXT_MIN_AGE_BARS
        <= int(context_age_bars)
        <= SPOT_CONTEXT_MAX_AGE_BARS
    )


__all__ = [
    "SPOT_CONTEXT_ACCEPTANCE_DISTANCE_ATR",
    "SPOT_CONTEXT_ACCEPTANCE_FLOW_3M_MIN",
    "SPOT_CONTEXT_INVALIDATION_ATR",
    "SPOT_CONTEXT_MAX_AGE_BARS",
    "SPOT_CONTEXT_MIN_AGE_BARS",
    "SPOT_EFFICIENCY_MIN",
    "SPOT_FLOW_15S_MIN",
    "SPOT_FLOW_60S_MIN",
    "SPOT_LEAD_BPS_MIN",
    "SPOT_NOTIONAL_BURST_MIN",
    "SPOT_PRICE_MOVE_BPS_MIN",
    "TWO_TO_ONE_IMBALANCE",
    "spot_context_accepted",
    "spot_context_entry_eligible",
    "spot_context_invalidated",
    "spot_led_repricing_direction",
]
