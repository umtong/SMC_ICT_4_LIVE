"""Pure causal predicates for Candidate 05 tail-flow liquidity reversals."""
from __future__ import annotations

import math


SWEEP_TAIL_IMPROVEMENT_MIN = 0.10
CHOCH_ACTIVE_TAIL_MIN = -0.10
CHOCH_PASSIVE_TAIL_MIN = -0.25
CHOCH_MATURE_FLOW_MAX = 0.50
CHOCH_PASSIVE_DEPTH_MIN = 0.10
MIN_LIQUIDITY_TARGET_NET_R = 0.40
MAX_LIQUIDITY_TARGET_NET_R = 1.50
FALLBACK_TARGET_NET_R = 0.75


def _finite(*values: float) -> bool:
    return all(math.isfinite(float(value)) for value in values)


def directional_tail_improvement(*, side: int, flow_15s: float, flow_60s: float) -> float:
    """Final-15-second aggressor-flow improvement versus the full minute."""
    if side not in (-1, 1):
        raise ValueError("side must be -1 or 1")
    if not _finite(flow_15s, flow_60s):
        return -math.inf
    return side * (float(flow_15s) - float(flow_60s))


def sweep_tail_recovers(
    *,
    side: int,
    flow_15s: float,
    flow_60s: float,
    minimum: float = SWEEP_TAIL_IMPROVEMENT_MIN,
) -> bool:
    """Whether aggressor flow materially turns toward the proposed reversal."""
    return directional_tail_improvement(
        side=side,
        flow_15s=flow_15s,
        flow_60s=flow_60s,
    ) >= minimum


def choch_flow_state(
    *,
    side: int,
    flow_15s: float,
    flow_3m: float,
    depth_imbalance: float,
) -> str | None:
    """Classify an early CHoCH as actively or passively sponsored.

    ``ACTIVE_CONFIRMATION`` means the final fifteen seconds have not materially
    reverted against the proposed reversal. ``PASSIVE_ROTATION`` admits a modest
    counter-flow tail only when the resting book still has a ten-percentage-point
    edge in the reversal direction. A strongly one-sided three-minute flow is
    rejected because the CHoCH is already a mature move rather than an early
    transition.
    """
    if side not in (-1, 1):
        raise ValueError("side must be -1 or 1")
    if not _finite(flow_15s, flow_3m, depth_imbalance):
        return None
    directional_15s = side * float(flow_15s)
    directional_3m = side * float(flow_3m)
    directional_depth = side * float(depth_imbalance)
    if directional_3m >= CHOCH_MATURE_FLOW_MAX:
        return None
    if directional_15s >= CHOCH_ACTIVE_TAIL_MIN:
        return "ACTIVE_CONFIRMATION"
    if (
        directional_15s >= CHOCH_PASSIVE_TAIL_MIN
        and directional_depth >= CHOCH_PASSIVE_DEPTH_MIN
    ):
        return "PASSIVE_ROTATION"
    return None


__all__ = [
    "CHOCH_ACTIVE_TAIL_MIN",
    "CHOCH_MATURE_FLOW_MAX",
    "CHOCH_PASSIVE_DEPTH_MIN",
    "CHOCH_PASSIVE_TAIL_MIN",
    "FALLBACK_TARGET_NET_R",
    "MAX_LIQUIDITY_TARGET_NET_R",
    "MIN_LIQUIDITY_TARGET_NET_R",
    "SWEEP_TAIL_IMPROVEMENT_MIN",
    "choch_flow_state",
    "directional_tail_improvement",
    "sweep_tail_recovers",
]
