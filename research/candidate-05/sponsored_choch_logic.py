"""Pure predicates for cost-aware participation at a sponsored CHoCH."""
from __future__ import annotations

import math


# A normalized aggressor imbalance of 1/3 is a favorable-to-opposing flow
# ratio of 2:1: (2 - 1) / (2 + 1). Immediate participation is reserved for the
# earlier transition before three-minute flow reaches that mature imbalance.
EARLY_CHOCH_DIRECTIONAL_FLOW_MAX = 1.0 / 3.0


def sponsored_choch_flow_phase_ready(
    *,
    side: int,
    flow_3m: float,
    maximum: float = EARLY_CHOCH_DIRECTIONAL_FLOW_MAX,
) -> bool:
    """Whether three-minute flow is early, aligned, and not yet mature.

    Negative mirrored flow means the final 15-second turn has not propagated
    through the broader auction. A mirrored value at or above one third means
    the three-minute aggressor ratio is already at least 2:1 and the CHoCH is no
    longer an early transition suitable for immediate participation. Those
    states remain eligible for the existing one-minute path observation.
    """
    if side not in (-1, 1):
        raise ValueError("side must be -1 or 1")
    if not math.isfinite(float(flow_3m)) or not math.isfinite(float(maximum)):
        return False
    if maximum <= 0.0:
        return False
    directional = side * float(flow_3m)
    return 0.0 <= directional < float(maximum)


def sponsored_choch_participation_ready(
    *,
    flow_state: str | None,
    side: int,
    flow_15s: float,
    current_depth_imbalance: float,
    setup_depth_imbalance: float,
    target_handoff: bool,
    minimum_depth: float,
) -> bool:
    """Whether CHoCH evidence is strong enough to participate immediately.

    Ordinary sweeps are often several minutes old by CHoCH, so their resting
    depth must still support the reversal at confirmation. A target-handoff
    setup is classified only after a fresh delayed reclaim with supportive
    depth; for that branch, the setup-time book is the recent causal evidence
    and active aligned aggressor flow may carry the final displacement.

    No fitted magnitude is introduced: aggressor flow must merely be aligned
    (> 0 after mirroring), and depth uses the existing directional minimum.
    """
    if side not in (-1, 1):
        raise ValueError("side must be -1 or 1")
    values = (
        flow_15s,
        current_depth_imbalance,
        setup_depth_imbalance,
        minimum_depth,
    )
    if flow_state != "ACTIVE_CONFIRMATION" or not all(
        math.isfinite(float(value)) for value in values
    ):
        return False
    if side * float(flow_15s) <= 0.0:
        return False
    depth = setup_depth_imbalance if target_handoff else current_depth_imbalance
    return side * float(depth) >= float(minimum_depth)


def slippage_protected_marketable_limit(
    *,
    observed_price: float,
    side: int,
    adverse_slippage_rate: float,
    price_increment: float,
) -> float:
    """Round a marketable limit through the configured adverse slippage.

    A long cap is rounded upward and a short cap downward, so a fill at the
    configured adverse slippage remains executable without an unbounded market
    order. Risk sizing and target selection must use this returned worst price.
    """
    if side not in (-1, 1):
        raise ValueError("side must be -1 or 1")
    values = (observed_price, adverse_slippage_rate, price_increment)
    if not all(math.isfinite(float(value)) for value in values):
        return math.nan
    if observed_price <= 0.0 or adverse_slippage_rate < 0.0 or price_increment <= 0.0:
        return math.nan
    raw = float(observed_price) * (1.0 + side * float(adverse_slippage_rate))
    scaled = raw / float(price_increment)
    ticks = math.ceil(scaled - 1e-12) if side > 0 else math.floor(scaled + 1e-12)
    return ticks * float(price_increment)


__all__ = [
    "EARLY_CHOCH_DIRECTIONAL_FLOW_MAX",
    "slippage_protected_marketable_limit",
    "sponsored_choch_flow_phase_ready",
    "sponsored_choch_participation_ready",
]
