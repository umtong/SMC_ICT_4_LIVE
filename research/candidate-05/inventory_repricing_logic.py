"""Causal predicates for Candidate 05 v39 inventory/repricing system.

The module contains no order, fill, PnL, margin or NAV simulation. It only
classifies completed observations into two economically distinct states:

* external inventory trap: a meaningful liquidity raid whose final flow and
  visible depth turn against the aggressor;
* quarter-hour repricing pullback: an efficient first-minute information burst
  creates directional context, then a later internal liquidity raid is absorbed
  in the same direction.
"""
from __future__ import annotations

import math


TWO_TO_ONE_IMBALANCE = 1.0 / 3.0
DEPTH_RATIO_3_TO_2_IMBALANCE = 0.20
EXTERNAL_PENETRATION_ATR_MIN = 1.0 / 3.0
EXTERNAL_TAIL_IMPROVEMENT_MIN = 0.50
EXTERNAL_DIRECTIONAL_DEPTH_MIN = DEPTH_RATIO_3_TO_2_IMBALANCE

QH_OPEN_FLOW_MIN = TWO_TO_ONE_IMBALANCE
QH_OPEN_NOTIONAL_BURST_MIN = 1.50
QH_OPEN_EFFICIENCY_MIN = 0.20
QH_OPEN_PRICE_MOVE_BPS_MIN = 2.0
QH_CONTEXT_MIN_AGE_BARS = 15
QH_CONTEXT_MAX_AGE_BARS = 75
QH_CONTEXT_ACCEPTED_DISTANCE_ATR = 0.50
QH_CONTEXT_INVALIDATION_ATR = 0.20

INTERNAL_PENETRATION_ATR_MIN = 0.20
INTERNAL_TAIL_IMPROVEMENT_MIN = 0.20
INTERNAL_DIRECTIONAL_DEPTH_MIN = 0.10


def _finite(*values: float) -> bool:
    return all(math.isfinite(float(value)) for value in values)


def directional_tail_improvement(
    *,
    side: int,
    flow_15s: float,
    flow_60s: float,
) -> float:
    if side not in (-1, 1):
        raise ValueError("side must be -1 or 1")
    if not _finite(flow_15s, flow_60s):
        return -math.inf
    return side * (float(flow_15s) - float(flow_60s))


def quarter_hour_repricing_direction(
    *,
    minute_of_hour: int,
    flow_open_10s: float,
    notional_open_10s_burst: float,
    ret_60s_bps: float,
    efficiency_60s: float,
) -> int:
    """Return the causal direction of a completed quarter-hour opening burst."""
    if minute_of_hour < 0 or minute_of_hour > 59:
        raise ValueError("minute_of_hour must be in [0, 59]")
    if minute_of_hour % 15 != 0:
        return 0
    if not _finite(
        flow_open_10s,
        notional_open_10s_burst,
        ret_60s_bps,
        efficiency_60s,
    ):
        return 0
    if abs(flow_open_10s) < QH_OPEN_FLOW_MIN:
        return 0
    direction = 1 if flow_open_10s > 0.0 else -1
    return direction if (
        notional_open_10s_burst >= QH_OPEN_NOTIONAL_BURST_MIN
        and direction * ret_60s_bps >= QH_OPEN_PRICE_MOVE_BPS_MIN
        and efficiency_60s >= QH_OPEN_EFFICIENCY_MIN
    ) else 0


def quarter_context_invalidated(
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
        return current_close < boundary_low - QH_CONTEXT_INVALIDATION_ATR * atr
    return current_close > boundary_high + QH_CONTEXT_INVALIDATION_ATR * atr


def quarter_context_accepted(
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
    return direction * (favorable_extreme - boundary_close) / atr >= QH_CONTEXT_ACCEPTED_DISTANCE_ATR


def inventory_trap_confirmed(
    *,
    side: int,
    penetration_atr: float,
    flow_15s: float,
    flow_60s: float,
    depth_imbalance: float,
    close: float,
    trade_vwap: float,
    external_or_clustered: bool,
) -> bool:
    """Whether a completed sweep shows reversal-side inventory sponsorship."""
    if side not in (-1, 1):
        raise ValueError("side must be -1 or 1")
    if not _finite(
        penetration_atr,
        flow_15s,
        flow_60s,
        depth_imbalance,
        close,
        trade_vwap,
    ):
        return False
    penetration_min = (
        EXTERNAL_PENETRATION_ATR_MIN
        if external_or_clustered
        else INTERNAL_PENETRATION_ATR_MIN
    )
    improvement_min = (
        EXTERNAL_TAIL_IMPROVEMENT_MIN
        if external_or_clustered
        else INTERNAL_TAIL_IMPROVEMENT_MIN
    )
    depth_min = (
        EXTERNAL_DIRECTIONAL_DEPTH_MIN
        if external_or_clustered
        else INTERNAL_DIRECTIONAL_DEPTH_MIN
    )
    return (
        penetration_atr >= penetration_min
        and directional_tail_improvement(
            side=side,
            flow_15s=flow_15s,
            flow_60s=flow_60s,
        ) >= improvement_min
        and side * depth_imbalance >= depth_min
        and side * (close - trade_vwap) >= 0.0
    )


def quarter_internal_sweep_eligible(
    *,
    setup_side: int,
    context_direction: int,
    context_age_bars: int,
    context_accepted: bool,
) -> bool:
    if setup_side not in (-1, 1) or context_direction not in (-1, 1):
        return False
    return (
        setup_side == context_direction
        and context_accepted
        and QH_CONTEXT_MIN_AGE_BARS <= context_age_bars <= QH_CONTEXT_MAX_AGE_BARS
    )


__all__ = [
    "DEPTH_RATIO_3_TO_2_IMBALANCE",
    "EXTERNAL_DIRECTIONAL_DEPTH_MIN",
    "EXTERNAL_PENETRATION_ATR_MIN",
    "EXTERNAL_TAIL_IMPROVEMENT_MIN",
    "INTERNAL_DIRECTIONAL_DEPTH_MIN",
    "INTERNAL_PENETRATION_ATR_MIN",
    "INTERNAL_TAIL_IMPROVEMENT_MIN",
    "QH_CONTEXT_ACCEPTED_DISTANCE_ATR",
    "QH_CONTEXT_INVALIDATION_ATR",
    "QH_CONTEXT_MAX_AGE_BARS",
    "QH_CONTEXT_MIN_AGE_BARS",
    "QH_OPEN_EFFICIENCY_MIN",
    "QH_OPEN_FLOW_MIN",
    "QH_OPEN_NOTIONAL_BURST_MIN",
    "QH_OPEN_PRICE_MOVE_BPS_MIN",
    "TWO_TO_ONE_IMBALANCE",
    "directional_tail_improvement",
    "inventory_trap_confirmed",
    "quarter_context_accepted",
    "quarter_context_invalidated",
    "quarter_hour_repricing_direction",
    "quarter_internal_sweep_eligible",
]
