"""Pure causal predicates for spot-led perpetual price discovery.

The spot leg defines an information-repricing context from completed spot
trades only. A later perpetual pullback uses a different evidence set—internal
liquidity, tail-flow recovery, visible depth and trade VWAP—to decide whether a
new auction leg has begun. The module contains no order, fill, account or PnL
logic.
"""
from __future__ import annotations

import math


SPOT_FLOW_MIN = 1.0 / 3.0  # favorable-to-opposing aggressor ratio >= 2:1
SPOT_NOTIONAL_BURST_MIN = 1.50
SPOT_EFFICIENCY_MIN = 0.20
SPOT_PRICE_MOVE_BPS_MIN = 2.0
SPOT_ACCEPTED_DISTANCE_ATR = 0.50
SPOT_CONTEXT_INVALIDATION_ATR = 0.20
SPOT_CONTEXT_MIN_PULLBACK_AGE_BARS = 3
SPOT_CONTEXT_MAX_AGE_BARS = 60
SPOT_PULLBACK_PENETRATION_ATR_MIN = 1.0 / 3.0
SPOT_PULLBACK_TAIL_IMPROVEMENT_MIN = 0.50
SPOT_PULLBACK_DIRECTIONAL_DEPTH_MIN = 0.10


def _finite(*values: float) -> bool:
    return all(math.isfinite(float(value)) for value in values)


def spot_led_direction(
    *,
    spot_ready: bool,
    spot_flow_15s: float,
    spot_flow_60s: float,
    spot_notional_burst: float,
    spot_ret_60s_bps: float,
    spot_efficiency_60s: float,
    perp_minus_spot_return_bps: float,
) -> int:
    """Return the direction of a completed spot-led information burst.

    A normalized imbalance of one third is a two-to-one aggressor ratio. Spot
    must carry that flow, unusual notional and an efficient same-direction price
    move. The same-minute spot return must exceed the perpetual return in the
    proposed direction; otherwise the event is not classified as spot-led.
    """
    values = (
        spot_flow_15s,
        spot_flow_60s,
        spot_notional_burst,
        spot_ret_60s_bps,
        spot_efficiency_60s,
        perp_minus_spot_return_bps,
    )
    if not spot_ready or not _finite(*values):
        return 0
    if abs(float(spot_flow_60s)) < SPOT_FLOW_MIN:
        return 0
    direction = 1 if float(spot_flow_60s) > 0.0 else -1
    spot_lead_bps = -direction * float(perp_minus_spot_return_bps)
    return direction if (
        direction * float(spot_flow_15s) >= 0.0
        and float(spot_notional_burst) >= SPOT_NOTIONAL_BURST_MIN
        and direction * float(spot_ret_60s_bps) >= SPOT_PRICE_MOVE_BPS_MIN
        and float(spot_efficiency_60s) >= SPOT_EFFICIENCY_MIN
        and spot_lead_bps > 0.0
    ) else 0


def spot_context_accepted(
    *,
    direction: int,
    boundary_close: float,
    favorable_extreme: float,
    atr: float,
) -> bool:
    if direction not in (-1, 1):
        raise ValueError("direction must be -1 or 1")
    if not _finite(boundary_close, favorable_extreme, atr) or atr <= 0.0:
        return False
    return (
        direction * (float(favorable_extreme) - float(boundary_close)) / float(atr)
        >= SPOT_ACCEPTED_DISTANCE_ATR
    )


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
        return float(current_close) < (
            float(boundary_low) - SPOT_CONTEXT_INVALIDATION_ATR * float(atr)
        )
    return float(current_close) > (
        float(boundary_high) + SPOT_CONTEXT_INVALIDATION_ATR * float(atr)
    )


def spot_context_pullback_eligible(*, accepted: bool, age_bars: int) -> bool:
    return (
        bool(accepted)
        and SPOT_CONTEXT_MIN_PULLBACK_AGE_BARS
        <= int(age_bars)
        <= SPOT_CONTEXT_MAX_AGE_BARS
    )


def spot_pullback_transfer_ready(
    *,
    direction: int,
    pool_kind: str,
    pool_level: float,
    previous_close: float,
    high: float,
    low: float,
    close: float,
    atr: float,
    flow_15s: float,
    flow_60s: float,
    depth_imbalance: float,
    trade_vwap: float,
    spot_flow_3m: float,
) -> bool:
    """Whether the first internal sweep starts a new continuation auction leg."""
    if direction not in (-1, 1):
        raise ValueError("direction must be -1 or 1")
    expected_kind = "LOW" if direction > 0 else "HIGH"
    if pool_kind != expected_kind:
        return False
    values = (
        pool_level,
        previous_close,
        high,
        low,
        close,
        atr,
        flow_15s,
        flow_60s,
        depth_imbalance,
        trade_vwap,
        spot_flow_3m,
    )
    if not _finite(*values) or atr <= 0.0 or high < low:
        return False

    if direction > 0:
        crossed = (
            previous_close >= pool_level
            and low <= pool_level - SPOT_PULLBACK_PENETRATION_ATR_MIN * atr
        )
        reclaimed = close > pool_level
        penetration = (pool_level - low) / atr
    else:
        crossed = (
            previous_close <= pool_level
            and high >= pool_level + SPOT_PULLBACK_PENETRATION_ATR_MIN * atr
        )
        reclaimed = close < pool_level
        penetration = (high - pool_level) / atr

    tail_improvement = direction * (float(flow_15s) - float(flow_60s))
    return (
        crossed
        and reclaimed
        and penetration >= SPOT_PULLBACK_PENETRATION_ATR_MIN
        and tail_improvement >= SPOT_PULLBACK_TAIL_IMPROVEMENT_MIN
        and direction * float(depth_imbalance)
        >= SPOT_PULLBACK_DIRECTIONAL_DEPTH_MIN
        and direction * (float(close) - float(trade_vwap)) >= 0.0
        and direction * float(spot_flow_3m) >= 0.0
    )


__all__ = [
    "SPOT_ACCEPTED_DISTANCE_ATR",
    "SPOT_CONTEXT_INVALIDATION_ATR",
    "SPOT_CONTEXT_MAX_AGE_BARS",
    "SPOT_CONTEXT_MIN_PULLBACK_AGE_BARS",
    "SPOT_EFFICIENCY_MIN",
    "SPOT_FLOW_MIN",
    "SPOT_NOTIONAL_BURST_MIN",
    "SPOT_PRICE_MOVE_BPS_MIN",
    "SPOT_PULLBACK_DIRECTIONAL_DEPTH_MIN",
    "SPOT_PULLBACK_PENETRATION_ATR_MIN",
    "SPOT_PULLBACK_TAIL_IMPROVEMENT_MIN",
    "spot_context_accepted",
    "spot_context_invalidated",
    "spot_context_pullback_eligible",
    "spot_led_direction",
    "spot_pullback_transfer_ready",
]
