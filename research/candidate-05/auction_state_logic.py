"""Pure causal state predicates for Candidate 05 v6."""
from __future__ import annotations

import math


FRESH_POOL_MAX_AGE_MINUTES = 5
DIRECTIONAL_DEPTH_MIN = 0.10
BREAKAWAY_INITIAL_RATIO = 2.0
POSITION_BUILDING_MIN_EFFICIENCY = 0.15


def directional_depth(side: int, imbalance: float) -> float:
    if side not in (-1, 1) or not math.isfinite(imbalance):
        return -math.inf
    return side * imbalance


def favorable_depth_ratio(side: int, imbalance: float) -> float:
    value = directional_depth(side, imbalance)
    if value == -math.inf or value <= -1.0:
        return 0.0
    if value >= 1.0:
        return math.inf
    return (1.0 + value) / (1.0 - value)


def reversal_depth_confirmed(
    *,
    side: int,
    sweep_imbalance: float,
    current_imbalance: float,
    pool_age_minutes: int,
) -> bool:
    """Depth must persist, except for a pool created in the latest 5m bar."""
    sweep = directional_depth(side, sweep_imbalance)
    current = directional_depth(side, current_imbalance)
    if sweep <= 0.0 or current <= 0.0:
        return False
    if pool_age_minutes <= FRESH_POOL_MAX_AGE_MINUTES:
        return True
    return current >= sweep or math.isclose(current, sweep, rel_tol=1e-12, abs_tol=1e-12)


def liquidation_breakaway_confirmed(
    *,
    side: int,
    sweep_imbalance: float,
    current_imbalance: float,
    oi_change_sweep_to_confirmation: float,
) -> bool:
    """Strong initial book plus non-expanding OI identifies exhaustion."""
    if not math.isfinite(oi_change_sweep_to_confirmation):
        return False
    return (
        favorable_depth_ratio(side, sweep_imbalance) >= BREAKAWAY_INITIAL_RATIO
        and directional_depth(side, current_imbalance) >= DIRECTIONAL_DEPTH_MIN
        and oi_change_sweep_to_confirmation <= 0.0
    )


def position_building_acceptance(
    *,
    accepted_distance_atr: float,
    directional_flow_15s: float,
    directional_flow_60s: float,
    efficiency_60s: float,
    consumed_side_depth_change: float,
    oi_change_15m: float,
) -> bool:
    """Price acceptance backed by new positions and withdrawn liquidity."""
    values = (
        accepted_distance_atr,
        directional_flow_15s,
        directional_flow_60s,
        efficiency_60s,
        consumed_side_depth_change,
        oi_change_15m,
    )
    if not all(math.isfinite(value) for value in values):
        return False
    return (
        accepted_distance_atr > 0.0
        and directional_flow_15s > 0.0
        and directional_flow_60s > 0.0
        and efficiency_60s >= POSITION_BUILDING_MIN_EFFICIENCY
        and consumed_side_depth_change < 0.0
        and oi_change_15m > 0.0
    )
