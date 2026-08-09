"""Pure geometry for a short-horizon draw on opposing liquidity."""
from __future__ import annotations

from dataclasses import dataclass
import math


MAX_REMAINING_TO_RECLAIM_MULTIPLE = 2.0


@dataclass(frozen=True, slots=True)
class TargetReachability:
    reachable: bool
    reason_code: str
    demonstrated_reclaim: float
    remaining_target_distance: float
    boundary_to_target_distance: float
    completion_fraction: float


def measured_move_target_reachability(
    *,
    side: int,
    session_boundary: float,
    confirmation_close: float,
    target: float,
    maximum_remaining_to_reclaim_multiple: float = MAX_REMAINING_TO_RECLAIM_MULTIPLE,
) -> TargetReachability:
    """Whether CHoCH has demonstrated enough path toward a frozen target.

    The confirmed move must have completed at least one third of the directional
    boundary-to-target path. Equivalently, no more than two demonstrated reclaim
    legs may remain. The test is mirror symmetric and contains no price, cost,
    fill or account simulation.
    """
    if side not in (-1, 1):
        raise ValueError("side must be -1 or 1")
    if maximum_remaining_to_reclaim_multiple <= 0.0:
        raise ValueError("remaining-to-reclaim multiple must be positive")
    values = (session_boundary, confirmation_close, target)
    if not all(math.isfinite(float(value)) for value in values):
        return TargetReachability(
            reachable=False,
            reason_code="REACHABILITY_GEOMETRY_IS_NOT_FINITE",
            demonstrated_reclaim=math.nan,
            remaining_target_distance=math.nan,
            boundary_to_target_distance=math.nan,
            completion_fraction=math.nan,
        )

    demonstrated = side * (confirmation_close - session_boundary)
    remaining = side * (target - confirmation_close)
    total = side * (target - session_boundary)
    fraction = demonstrated / total if total > 0.0 else math.nan
    if demonstrated <= 0.0 or remaining <= 0.0 or total <= 0.0:
        return TargetReachability(
            reachable=False,
            reason_code="REACHABILITY_DIRECTIONAL_GEOMETRY_INVALID",
            demonstrated_reclaim=demonstrated,
            remaining_target_distance=remaining,
            boundary_to_target_distance=total,
            completion_fraction=fraction,
        )
    if remaining > maximum_remaining_to_reclaim_multiple * demonstrated + 1e-12:
        return TargetReachability(
            reachable=False,
            reason_code=(
                "OPPOSING_LIQUIDITY_TARGET_NOT_REACHABLE_FROM_CONFIRMED_RECLAIM"
            ),
            demonstrated_reclaim=demonstrated,
            remaining_target_distance=remaining,
            boundary_to_target_distance=total,
            completion_fraction=fraction,
        )
    return TargetReachability(
        reachable=True,
        reason_code="OPPOSING_LIQUIDITY_TARGET_REACHABLE_BY_MEASURED_MOVE",
        demonstrated_reclaim=demonstrated,
        remaining_target_distance=remaining,
        boundary_to_target_distance=total,
        completion_fraction=fraction,
    )


__all__ = [
    "MAX_REMAINING_TO_RECLAIM_MULTIPLE",
    "TargetReachability",
    "measured_move_target_reachability",
]
