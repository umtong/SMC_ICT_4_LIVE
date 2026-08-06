"""Pure causal flow-phase predicates for position-building balance acceptance."""
from __future__ import annotations

import math


TWO_TO_ONE_AGGRESSOR_SHARE = 1.0 / 3.0
EARLY_RESET_REACCELERATION = "EARLY_RESET_REACCELERATION"
MATURE_AT_BREAKOUT = "MATURE_AT_BREAKOUT"
NO_DIRECTIONAL_BREAKOUT = "NO_DIRECTIONAL_BREAKOUT"
NO_BROAD_FLOW_RESET = "NO_BROAD_FLOW_RESET"
NO_TAIL_REACCELERATION = "NO_TAIL_REACCELERATION"
INVALID_OBSERVATION = "INVALID_OBSERVATION"


def position_building_flow_phase(
    *,
    side: int,
    breakout_flow_3m: float,
    retest_flow_3m: float,
    retest_flow_15s: float,
) -> str:
    """Classify whether a balance break reset before its first retest entry.

    Normalized signed flow is mirrored by ``side``. A valid continuation is not
    an already mature impulse chased at the retest. It begins while broad
    three-minute aggressor flow is positive but below a 2:1 directional ratio,
    then broad flow cools before the first retest, and the final fifteen seconds
    reaccelerate in the original direction as price reclaims the boundary.

    The one-third boundary is algebraic rather than fitted: for normalized
    imbalance ``(buy - sell) / (buy + sell)``, one third is exactly a 2:1
    aggressor ratio. No magnitude is required for the reacceleration beyond its
    being directional and stronger than the cooled three-minute state.
    """
    if side not in (-1, 1):
        raise ValueError("side must be -1 or 1")
    values = (breakout_flow_3m, retest_flow_3m, retest_flow_15s)
    if not all(math.isfinite(float(value)) for value in values):
        return INVALID_OBSERVATION

    breakout = side * float(breakout_flow_3m)
    retest_broad = side * float(retest_flow_3m)
    retest_tail = side * float(retest_flow_15s)

    if breakout <= 0.0:
        return NO_DIRECTIONAL_BREAKOUT
    if breakout >= TWO_TO_ONE_AGGRESSOR_SHARE:
        return MATURE_AT_BREAKOUT
    if retest_broad < 0.0 or retest_broad >= breakout:
        return NO_BROAD_FLOW_RESET
    if retest_tail <= retest_broad:
        return NO_TAIL_REACCELERATION
    return EARLY_RESET_REACCELERATION


def position_building_flow_phase_ready(
    *,
    side: int,
    breakout_flow_3m: float,
    retest_flow_3m: float,
    retest_flow_15s: float,
) -> bool:
    return position_building_flow_phase(
        side=side,
        breakout_flow_3m=breakout_flow_3m,
        retest_flow_3m=retest_flow_3m,
        retest_flow_15s=retest_flow_15s,
    ) == EARLY_RESET_REACCELERATION


__all__ = [
    "EARLY_RESET_REACCELERATION",
    "INVALID_OBSERVATION",
    "MATURE_AT_BREAKOUT",
    "NO_BROAD_FLOW_RESET",
    "NO_DIRECTIONAL_BREAKOUT",
    "NO_TAIL_REACCELERATION",
    "TWO_TO_ONE_AGGRESSOR_SHARE",
    "position_building_flow_phase",
    "position_building_flow_phase_ready",
]
