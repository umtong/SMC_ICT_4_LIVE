"""Pure causal predicates for post-funding forced-position resets."""
from __future__ import annotations

import math


POST_FUNDING_MINUTE_START = 6
POST_FUNDING_MINUTE_END = 20
TAIL_IMPROVEMENT_MIN = 0.50
DIRECTIONAL_DEPTH_MIN = 0.20
MIN_CROWDING_BASIS_BPS = 1.0


def funding_cycle_key(ts_event_ns: int) -> int:
    """Return the fixed eight-hour UTC settlement cycle containing the event."""
    if int(ts_event_ns) < 0:
        raise ValueError("ts_event_ns must be non-negative")
    minute = int(ts_event_ns) // 60_000_000_000
    return minute // (8 * 60)


def minutes_after_funding(ts_event_ns: int) -> int:
    if int(ts_event_ns) < 0:
        raise ValueError("ts_event_ns must be non-negative")
    minute = int(ts_event_ns) // 60_000_000_000
    return minute % (8 * 60)


def in_post_funding_window(ts_event_ns: int) -> bool:
    minute = minutes_after_funding(ts_event_ns)
    return POST_FUNDING_MINUTE_START <= minute <= POST_FUNDING_MINUTE_END


def funding_reset_side(
    *,
    pre_funding_basis_bps: float,
    normal_basis_bps: float,
    perp_minus_spot_return_bps: float,
) -> int:
    """Map payer crowding and post-settlement forced move to reversion side.

    Positive pre-funding excess basis denotes crowded longs paying shorts. If
    perpetual then underperforms spot after settlement, the reversion side is
    long. Negative excess basis and post-settlement outperformance mirror this.
    """
    values = (
        pre_funding_basis_bps,
        normal_basis_bps,
        perp_minus_spot_return_bps,
    )
    if not all(math.isfinite(float(value)) for value in values):
        return 0
    crowding = float(pre_funding_basis_bps) - float(normal_basis_bps)
    relative_move = float(perp_minus_spot_return_bps)
    if crowding >= MIN_CROWDING_BASIS_BPS and relative_move < 0.0:
        return 1
    if crowding <= -MIN_CROWDING_BASIS_BPS and relative_move > 0.0:
        return -1
    return 0


def funding_forced_reset_confirmed(
    *,
    side: int,
    oi_change_5m: float,
    flow_15s: float,
    flow_60s: float,
    depth_imbalance: float,
) -> bool:
    if side not in (-1, 1):
        raise ValueError("side must be -1 or 1")
    values = (oi_change_5m, flow_15s, flow_60s, depth_imbalance)
    if not all(math.isfinite(float(value)) for value in values):
        return False
    return (
        float(oi_change_5m) < 0.0
        and side * (float(flow_15s) - float(flow_60s)) >= TAIL_IMPROVEMENT_MIN
        and side * float(depth_imbalance) >= DIRECTIONAL_DEPTH_MIN
    )


__all__ = [
    "DIRECTIONAL_DEPTH_MIN",
    "MIN_CROWDING_BASIS_BPS",
    "POST_FUNDING_MINUTE_END",
    "POST_FUNDING_MINUTE_START",
    "TAIL_IMPROVEMENT_MIN",
    "funding_cycle_key",
    "funding_forced_reset_confirmed",
    "funding_reset_side",
    "in_post_funding_window",
    "minutes_after_funding",
]
