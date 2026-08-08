"""Pure predicates for cross-market external-liquidity exhaustion.

A parent shock is not a reversal signal by itself.  It identifies an auction
which reached important external liquidity with unusually large, synchronized
spot/perpetual participation but poor price efficiency.  A trade is allowed
only after a strictly later microstructure break and perpetual aggressor-flow
reversal.  Incomplete or ambiguous observations are unresolved/no trade.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import math


class ParticipationRoute(StrEnum):
    EXTERNAL_LIQUIDITY_EXHAUSTION = "EXTERNAL_LIQUIDITY_EXHAUSTION"
    UNRESOLVED = "UNRESOLVED"


@dataclass(frozen=True, slots=True)
class ParentParticipation:
    route: ParticipationRoute
    reason: str


def classify_parent_exhaustion(
    *,
    direction: int,
    perp_return_bps: float,
    atr_bps: float,
    perp_flow: float,
    spot_return_bps: float,
    spot_flow: float,
    notional_burst: float,
    efficiency: float,
    perp_touched_external_edge: bool,
    spot_touched_external_edge: bool,
    min_return_bps: float,
    min_displacement_atr: float,
    min_perp_flow: float,
    min_spot_flow: float,
    min_notional_burst: float,
    max_efficiency: float,
) -> ParentParticipation:
    """Classify a completed parent minute without using later evidence."""
    if direction not in (-1, 1):
        raise ValueError("direction must be -1 or +1")
    values = (
        perp_return_bps,
        atr_bps,
        perp_flow,
        spot_return_bps,
        spot_flow,
        notional_burst,
        efficiency,
        min_return_bps,
        min_displacement_atr,
        min_perp_flow,
        min_spot_flow,
        min_notional_burst,
        max_efficiency,
    )
    if not all(math.isfinite(value) for value in values) or atr_bps <= 0.0:
        return ParentParticipation(
            ParticipationRoute.UNRESOLVED,
            "INCOMPLETE_EXHAUSTION_OBSERVATION",
        )

    displacement_hurdle = max(min_return_bps, min_displacement_atr * atr_bps)
    signed_perp_return = direction * perp_return_bps
    signed_spot_return = direction * spot_return_bps
    signed_perp_flow = direction * perp_flow
    signed_spot_flow = direction * spot_flow

    if not perp_touched_external_edge or not spot_touched_external_edge:
        return ParentParticipation(
            ParticipationRoute.UNRESOLVED,
            "SHOCK_DID_NOT_REACH_EXTERNAL_LIQUIDITY_IN_BOTH_MARKETS",
        )
    if signed_perp_return < displacement_hurdle:
        return ParentParticipation(
            ParticipationRoute.UNRESOLVED,
            "DISPLACEMENT_DID_NOT_CLEAR_VOLATILITY_AND_COST_HURDLE",
        )
    if signed_spot_return <= 0.0:
        return ParentParticipation(
            ParticipationRoute.UNRESOLVED,
            "SPOT_DID_NOT_CONFIRM_SHOCK_DIRECTION",
        )
    if signed_perp_flow < min_perp_flow or signed_spot_flow < min_spot_flow:
        return ParentParticipation(
            ParticipationRoute.UNRESOLVED,
            "SPOT_AND_PERPETUAL_AGGRESSOR_FLOW_NOT_SYNCHRONIZED",
        )
    if notional_burst < min_notional_burst:
        return ParentParticipation(
            ParticipationRoute.UNRESOLVED,
            "PARTICIPATION_WAS_NOT_A_VOLUME_CLIMAX",
        )
    if efficiency > max_efficiency:
        return ParentParticipation(
            ParticipationRoute.UNRESOLVED,
            "SHOCK_RETAINED_TOO_MUCH_DIRECTIONAL_PRICE_EFFICIENCY",
        )

    return ParentParticipation(
        ParticipationRoute.EXTERNAL_LIQUIDITY_EXHAUSTION,
        "SYNCHRONIZED_FLOW_CLIMAX_AT_EXTERNAL_LIQUIDITY_WITH_LOW_EFFICIENCY",
    )


def exhaustion_transition_confirmed(
    *,
    event_direction: int,
    close: float,
    prior_high: float,
    prior_low: float,
    perp_flow: float,
) -> bool:
    """Confirm a distinct later reversal leg with price and flow evidence."""
    if event_direction not in (-1, 1):
        return False
    values = (close, prior_high, prior_low, perp_flow)
    if not all(math.isfinite(value) for value in values):
        return False
    structure_broken = close < prior_low if event_direction > 0 else close > prior_high
    flow_reversed = event_direction * perp_flow < 0.0
    return structure_broken and flow_reversed


__all__ = [
    "ParentParticipation",
    "ParticipationRoute",
    "classify_parent_exhaustion",
    "exhaustion_transition_confirmed",
]
