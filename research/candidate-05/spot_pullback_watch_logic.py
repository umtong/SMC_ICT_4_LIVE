"""Pure predicates for resolving one spot-led internal pullback episode.

A liquidity penetration is an interaction, not a completed trade signal.  This
module lets the same pullback leg reveal whether defense appears within three
completed observations.  It does not search for a later unrelated setup and it
contains no execution, accounting or PnL logic.
"""
from __future__ import annotations

import math

from spot_price_discovery_logic import SPOT_PULLBACK_DIRECTIONAL_DEPTH_MIN
from spot_price_discovery_logic import SPOT_PULLBACK_TAIL_IMPROVEMENT_MIN


SPOT_PULLBACK_RESPONSE_BARS = 3


def _finite(*values: float) -> bool:
    return all(math.isfinite(float(value)) for value in values)


def update_pullback_extreme(
    *,
    direction: int,
    current_extreme: float,
    high: float,
    low: float,
) -> float:
    if direction not in (-1, 1):
        raise ValueError("direction must be -1 or 1")
    if not _finite(current_extreme, high, low) or high < low:
        return math.nan
    return min(float(current_extreme), float(low)) if direction > 0 else max(
        float(current_extreme),
        float(high),
    )


def pullback_response_expired(*, age_bars: int) -> bool:
    return int(age_bars) >= SPOT_PULLBACK_RESPONSE_BARS


def spot_pullback_defense_ready(
    *,
    direction: int,
    level: float,
    close: float,
    flow_15s: float,
    flow_60s: float,
    depth_imbalance: float,
    trade_vwap: float,
    spot_flow_3m: float,
) -> bool:
    """Whether the penetrated level is now defended by independent evidence."""
    if direction not in (-1, 1):
        raise ValueError("direction must be -1 or 1")
    values = (
        level,
        close,
        flow_15s,
        flow_60s,
        depth_imbalance,
        trade_vwap,
        spot_flow_3m,
    )
    if not _finite(*values):
        return False
    reclaimed = float(close) > float(level) if direction > 0 else float(close) < float(level)
    tail_improvement = direction * (float(flow_15s) - float(flow_60s))
    return (
        reclaimed
        and tail_improvement >= SPOT_PULLBACK_TAIL_IMPROVEMENT_MIN
        and direction * float(depth_imbalance)
        >= SPOT_PULLBACK_DIRECTIONAL_DEPTH_MIN
        and direction * (float(close) - float(trade_vwap)) >= 0.0
        and direction * float(spot_flow_3m) >= 0.0
    )


__all__ = [
    "SPOT_PULLBACK_RESPONSE_BARS",
    "pullback_response_expired",
    "spot_pullback_defense_ready",
    "update_pullback_extreme",
]
