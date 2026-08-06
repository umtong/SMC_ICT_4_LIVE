"""Pure causal predicates for delayed rejection after a liquidity access."""
from __future__ import annotations

import math

from depth_logic import DIRECTIONAL_DEPTH_MIN
from flow_inflection_logic import SWEEP_TAIL_IMPROVEMENT_MIN


# A completed three-minute flow observation is the natural response horizon.
DELAYED_RESPONSE_BARS = 3
# Once response is visible, retain the existing Candidate 05 CHoCH horizon.
DELAYED_CHOCH_BARS = 4


def delayed_access_is_material(
    *,
    penetration_atr: float,
    notional_burst: float,
    minimum_penetration_atr: float,
    minimum_notional_burst: float,
) -> bool:
    """Keep only an actual penetrative, active liquidity access."""
    values = (
        penetration_atr,
        notional_burst,
        minimum_penetration_atr,
        minimum_notional_burst,
    )
    return all(math.isfinite(float(value)) for value in values) and (
        penetration_atr >= minimum_penetration_atr
        and notional_burst >= minimum_notional_burst
    )


def delayed_rejection_response(
    *,
    side: int,
    pool_kind: str,
    boundary: float,
    close: float,
    flow_15s: float,
    flow_60s: float,
    depth_imbalance: float,
    minimum_tail_improvement: float = SWEEP_TAIL_IMPROVEMENT_MIN,
    minimum_directional_depth: float = DIRECTIONAL_DEPTH_MIN,
) -> bool:
    """Whether a later completed bar makes an unresolved sweep a rejection.

    A high-pool access must close back below the consumed high and a low-pool
    access must close back above the consumed low.  The final fifteen seconds
    must improve versus the full-minute aggressor flow in the reversal
    direction, while current resting depth supports that same direction.
    """
    if side not in (-1, 1):
        raise ValueError("side must be -1 or 1")
    if pool_kind not in {"HIGH", "LOW"}:
        raise ValueError("pool_kind must be HIGH or LOW")
    expected_side = -1 if pool_kind == "HIGH" else 1
    if side != expected_side:
        return False
    values = (
        boundary,
        close,
        flow_15s,
        flow_60s,
        depth_imbalance,
        minimum_tail_improvement,
        minimum_directional_depth,
    )
    if not all(math.isfinite(float(value)) for value in values):
        return False

    reclaimed = close < boundary if pool_kind == "HIGH" else close > boundary
    tail_improvement = side * (flow_15s - flow_60s)
    directional_depth = side * depth_imbalance
    return (
        reclaimed
        and tail_improvement >= minimum_tail_improvement
        and directional_depth >= minimum_directional_depth
    )


__all__ = [
    "DELAYED_CHOCH_BARS",
    "DELAYED_RESPONSE_BARS",
    "delayed_access_is_material",
    "delayed_rejection_response",
]
