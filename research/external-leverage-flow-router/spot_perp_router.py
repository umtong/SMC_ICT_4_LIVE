"""Pure state predicates for spot-led discovery and perp-led crowding."""
from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import math


class ParticipationRoute(StrEnum):
    SPOT_LED_ACCEPTANCE = "SPOT_LED_ACCEPTANCE"
    PERP_LED_CROWDING = "PERP_LED_CROWDING"
    UNRESOLVED = "UNRESOLVED"


@dataclass(frozen=True, slots=True)
class ParentParticipation:
    route: ParticipationRoute
    reason: str


def classify_parent_participation(
    *,
    direction: int,
    spot_accepted_edge: bool,
    perp_return_bps: float,
    spot_return_bps: float,
    perp_flow: float,
    spot_flow: float,
    basis_change_bps: float,
) -> ParentParticipation:
    if direction not in (-1, 1):
        raise ValueError("direction must be -1 or +1")
    values = (
        perp_return_bps,
        spot_return_bps,
        perp_flow,
        spot_flow,
        basis_change_bps,
    )
    if not all(math.isfinite(value) for value in values):
        return ParentParticipation(
            ParticipationRoute.UNRESOLVED,
            "INCOMPLETE_SPOT_PERP_PARTICIPATION_OBSERVATION",
        )

    signed_perp_return = direction * perp_return_bps
    signed_spot_return = direction * spot_return_bps
    signed_perp_flow = direction * perp_flow
    signed_spot_flow = direction * spot_flow
    signed_basis_change = direction * basis_change_bps

    spot_led = (
        spot_accepted_edge
        and signed_spot_return > 0.0
        and signed_spot_flow > 0.0
        and signed_spot_return >= signed_perp_return
        and signed_basis_change <= 0.0
    )
    if spot_led:
        return ParentParticipation(
            ParticipationRoute.SPOT_LED_ACCEPTANCE,
            "SPOT_ACCEPTED_EDGE_AND_LED_RETURN_WITHOUT_PERP_PREMIUM_EXPANSION",
        )

    perp_led = (
        not spot_accepted_edge
        and signed_perp_return > 0.0
        and signed_perp_flow > 0.0
        and signed_perp_return > signed_spot_return
        and signed_basis_change > 0.0
    )
    if perp_led:
        return ParentParticipation(
            ParticipationRoute.PERP_LED_CROWDING,
            "PERP_LED_BREAK_WITH_OI_EXPANSION_AND_SPOT_NONCONFIRMATION",
        )

    return ParentParticipation(
        ParticipationRoute.UNRESOLVED,
        "BREAKOUT_PARTICIPATION_WAS_NEITHER_SPOT_LED_NOR_CLEAN_PERP_CROWDING",
    )


def spot_led_retest_confirmed(
    *,
    side: int,
    boundary: float,
    high: float,
    low: float,
    close: float,
    atr: float,
    touch_tolerance_atr: float,
    spot_flow: float,
    perp_flow: float,
    basis_change_bps: float,
) -> bool:
    if side not in (-1, 1) or atr <= 0.0:
        return False
    touched = (
        low <= boundary + touch_tolerance_atr * atr
        if side > 0
        else high >= boundary - touch_tolerance_atr * atr
    )
    held = close > boundary if side > 0 else close < boundary
    return (
        touched
        and held
        and side * spot_flow > 0.0
        and side * perp_flow > 0.0
        and side * basis_change_bps <= 0.0
    )


def perp_crowding_failure_confirmed(
    *,
    event_direction: int,
    boundary: float,
    close: float,
    spot_flow: float,
    perp_flow: float,
    basis_change_bps: float,
) -> bool:
    if event_direction not in (-1, 1):
        return False
    reentered = close < boundary if event_direction > 0 else close > boundary
    reversal_side = -event_direction
    return (
        reentered
        and event_direction * basis_change_bps < 0.0
        and reversal_side * perp_flow > 0.0
        and reversal_side * spot_flow >= 0.0
    )


__all__ = [
    "ParentParticipation",
    "ParticipationRoute",
    "classify_parent_participation",
    "perp_crowding_failure_confirmed",
    "spot_led_retest_confirmed",
]
