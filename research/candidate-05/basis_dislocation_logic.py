"""Pure causal predicates for forced perpetual/spot basis dislocations."""
from __future__ import annotations

import math
from statistics import median
from typing import Sequence


ROBUST_SIGMA_MULTIPLIER = 1.4826
DISLOCATION_SIGMA_MIN = 3.0
MIN_ABSOLUTE_DISLOCATION_BPS = 1.0
TAIL_IMPROVEMENT_MIN = 0.50
DIRECTIONAL_DEPTH_MIN = 0.20


def robust_basis_location_scale(history_bps: Sequence[float]) -> tuple[float, float]:
    values = [float(value) for value in history_bps if math.isfinite(float(value))]
    if not values:
        return float("nan"), float("nan")
    location = float(median(values))
    mad = float(median(abs(value - location) for value in values))
    return location, ROBUST_SIGMA_MULTIPLIER * mad


def basis_dislocation_side(
    *,
    current_basis_bps: float,
    history_bps: Sequence[float],
    minimum_history: int = 60,
) -> tuple[int, float, float]:
    """Return the mean-reversion side and prior-only robust basis state."""
    values = [float(value) for value in history_bps if math.isfinite(float(value))]
    if len(values) < int(minimum_history) or not math.isfinite(float(current_basis_bps)):
        return 0, float("nan"), float("nan")
    location, scale = robust_basis_location_scale(values)
    threshold = max(
        MIN_ABSOLUTE_DISLOCATION_BPS,
        DISLOCATION_SIGMA_MIN * max(scale, 0.0),
    )
    deviation = float(current_basis_bps) - location
    if deviation >= threshold:
        return -1, location, scale
    if deviation <= -threshold:
        return 1, location, scale
    return 0, location, scale


def forced_perpetual_dislocation_confirmed(
    *,
    side: int,
    perp_minus_spot_return_bps: float,
    oi_change_5m: float,
    flow_15s: float,
    flow_60s: float,
    depth_imbalance: float,
) -> bool:
    """Confirm that perpetual led away, OI contracted, and tail flow turned back."""
    if side not in (-1, 1):
        raise ValueError("side must be -1 or 1")
    values = (
        perp_minus_spot_return_bps,
        oi_change_5m,
        flow_15s,
        flow_60s,
        depth_imbalance,
    )
    if not all(math.isfinite(float(value)) for value in values):
        return False
    perpetual_moved_against_reversion = (
        -side * float(perp_minus_spot_return_bps) > 0.0
    )
    tail_improvement = side * (float(flow_15s) - float(flow_60s))
    return (
        perpetual_moved_against_reversion
        and float(oi_change_5m) < 0.0
        and tail_improvement >= TAIL_IMPROVEMENT_MIN
        and side * float(depth_imbalance) >= DIRECTIONAL_DEPTH_MIN
    )


def spot_implied_perpetual_price(
    *,
    spot_price: float,
    normal_basis_bps: float,
) -> float:
    if not math.isfinite(float(spot_price)) or spot_price <= 0.0:
        return float("nan")
    if not math.isfinite(float(normal_basis_bps)):
        return float("nan")
    return float(spot_price) * math.exp(float(normal_basis_bps) / 10_000.0)


__all__ = [
    "DIRECTIONAL_DEPTH_MIN",
    "DISLOCATION_SIGMA_MIN",
    "MIN_ABSOLUTE_DISLOCATION_BPS",
    "ROBUST_SIGMA_MULTIPLIER",
    "TAIL_IMPROVEMENT_MIN",
    "basis_dislocation_side",
    "forced_perpetual_dislocation_confirmed",
    "robust_basis_location_scale",
    "spot_implied_perpetual_price",
]
