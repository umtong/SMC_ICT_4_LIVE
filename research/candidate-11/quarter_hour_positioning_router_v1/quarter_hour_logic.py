"""Pure quarter-hour positioning state router.

The opening ten-second order imbalance is the parent event.  Open-interest,
spot/perpetual price delivery, tail flow, and closing L1 pressure each have a
separate causal role.  This module has no orders, fills, PnL, or NAV.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import math


class QuarterRoute(str, Enum):
    NO_EVENT = "NO_EVENT"
    NEW_RISK_CONTINUATION = "NEW_RISK_CONTINUATION"
    FORCED_CLOSURE_REVERSAL = "FORCED_CLOSURE_REVERSAL"
    UNRESOLVED = "UNRESOLVED"


@dataclass(frozen=True, slots=True)
class QuarterObservation:
    opening_flow_10s: float
    opening_notional_burst: float
    perpetual_return_bps: float
    tail_flow_15s: float
    spot_flow_60s: float
    spot_return_bps: float
    oi_change_5m: float
    oi_value_change_5m: float
    l1_pressure_persisted: bool
    l1_pressure_flipped: bool

    def validate(self) -> None:
        values = (
            self.opening_flow_10s,
            self.opening_notional_burst,
            self.perpetual_return_bps,
            self.tail_flow_15s,
            self.spot_flow_60s,
            self.spot_return_bps,
            self.oi_change_5m,
            self.oi_value_change_5m,
        )
        if not all(math.isfinite(float(value)) for value in values):
            raise ValueError("quarter-hour observation must be finite")


@dataclass(frozen=True, slots=True)
class QuarterDecision:
    route: QuarterRoute
    parent_direction: int
    side: int
    reason: str


def route_quarter_hour(observation: QuarterObservation) -> QuarterDecision:
    """Route one completed quarter-hour opening minute.

    The event is admitted only when its first ten seconds carry more notional
    than the trailing causal median.  No outcome-fitted magnitude threshold is
    used.  Direction is defined solely by the opening ten-second imbalance.
    """

    observation.validate()
    if (
        observation.opening_notional_burst <= 1.0
        or observation.opening_flow_10s == 0.0
    ):
        return QuarterDecision(
            QuarterRoute.NO_EVENT,
            0,
            0,
            "OPENING_TEN_SECONDS_NOT_AN_ABOVE_MEDIAN_DIRECTIONAL_BURST",
        )

    direction = 1 if observation.opening_flow_10s > 0.0 else -1
    new_risk = (
        observation.oi_change_5m >= 0.0
        and observation.oi_value_change_5m >= 0.0
    )
    forced_closure = observation.oi_change_5m < 0.0

    continuation = (
        new_risk
        and direction * observation.perpetual_return_bps > 0.0
        and direction * observation.tail_flow_15s > 0.0
        and direction * observation.spot_flow_60s > 0.0
        and direction * observation.spot_return_bps > 0.0
        and observation.l1_pressure_persisted
    )
    if continuation:
        return QuarterDecision(
            QuarterRoute.NEW_RISK_CONTINUATION,
            direction,
            direction,
            "OPENING_ALGO_FLOW_BECAME_BROAD_NEW_RISK_PRICE_DISCOVERY",
        )

    reversal = (
        forced_closure
        and direction * observation.perpetual_return_bps < 0.0
        and direction * observation.tail_flow_15s < 0.0
        and direction * observation.spot_flow_60s < 0.0
        and direction * observation.spot_return_bps < 0.0
        and observation.l1_pressure_flipped
    )
    if reversal:
        return QuarterDecision(
            QuarterRoute.FORCED_CLOSURE_REVERSAL,
            direction,
            -direction,
            "OPENING_FORCED_FLOW_FAILED_AND_TRANSFERRED_TO_OPPOSITE_AUCTION",
        )

    return QuarterDecision(
        QuarterRoute.UNRESOLVED,
        direction,
        0,
        "POSITIONING_PRICE_DELIVERY_AND_LIQUIDITY_RESPONSE_DID_NOT_COHERE",
    )


__all__ = [
    "QuarterDecision",
    "QuarterObservation",
    "QuarterRoute",
    "route_quarter_hour",
]
