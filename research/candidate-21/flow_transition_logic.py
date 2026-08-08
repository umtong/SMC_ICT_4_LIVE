"""Pure state transition between a 10-second parent shock and response.

An accepted boundary attack is not automatically a medium-horizon carry event.
The immediate response must prove one of two economically distinct states:

1. persistent sponsorship: same-side normalized aggressor flow is at least as
   strong as the parent event; or
2. delayed transmission: the parent was initially inefficient, but the response
   converts that effort into strictly higher price efficiency and directional
   displacement.

If both flow and price efficiency decay, the event is treated as exhaustion or
absorption and closes no-trade.  No fitted magnitude threshold, PnL, execution,
accounting or future observation enters this module.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import StrEnum
import math
from typing import Any, Mapping


class FlowTransitionRoute(StrEnum):
    PERSISTENT_SPONSORSHIP = "PERSISTENT_SPONSORSHIP"
    DELAYED_PRICE_DISCOVERY = "DELAYED_PRICE_DISCOVERY"
    DECAYED_NO_TRADE = "DECAYED_NO_TRADE"


@dataclass(frozen=True, slots=True)
class FlowTransitionEvidence:
    direction: int
    event_open: float
    event_high: float
    event_low: float
    event_close: float
    event_flow: float
    response_flow: float
    response_return_bps: float
    response_efficiency: float

    def __post_init__(self) -> None:
        if self.direction not in (-1, 1):
            raise ValueError("direction must be -1 or +1")
        values = (
            self.event_open,
            self.event_high,
            self.event_low,
            self.event_close,
            self.event_flow,
            self.response_flow,
            self.response_return_bps,
            self.response_efficiency,
        )
        if any(not math.isfinite(float(value)) for value in values):
            raise ValueError("flow-transition inputs must be finite")
        if self.event_low > self.event_high:
            raise ValueError("event range is invalid")
        if not self.event_low <= self.event_open <= self.event_high:
            raise ValueError("event open lies outside range")
        if not self.event_low <= self.event_close <= self.event_high:
            raise ValueError("event close lies outside range")
        if self.event_low <= 0.0:
            raise ValueError("event prices must be positive")
        if abs(self.event_flow) > 1.0 + 1e-12:
            raise ValueError("event_flow must be normalized")
        if abs(self.response_flow) > 1.0 + 1e-12:
            raise ValueError("response_flow must be normalized")
        if not 0.0 <= self.response_efficiency <= 1.0 + 1e-12:
            raise ValueError("response_efficiency must be in [0, 1]")

    @property
    def directional_event_flow(self) -> float:
        return self.direction * self.event_flow

    @property
    def directional_response_flow(self) -> float:
        return self.direction * self.response_flow

    @property
    def directional_event_return_bps(self) -> float:
        return self.direction * math.log(
            self.event_close / self.event_open,
        ) * 10_000.0

    @property
    def event_range_bps(self) -> float:
        return math.log(self.event_high / self.event_low) * 10_000.0

    @property
    def event_efficiency(self) -> float:
        denominator = self.event_range_bps
        if denominator <= 0.0:
            return 0.0
        return min(
            1.0,
            abs(self.directional_event_return_bps) / denominator,
        )

    @property
    def directional_response_return_bps(self) -> float:
        return self.direction * self.response_return_bps


@dataclass(frozen=True, slots=True)
class FlowTransitionDecision:
    eligible: bool
    route: FlowTransitionRoute
    reason: str
    evidence: FlowTransitionEvidence

    def details(self) -> dict[str, Any]:
        values = asdict(self.evidence)
        values.update(
            {
                "directional_event_flow": (
                    self.evidence.directional_event_flow
                ),
                "directional_response_flow": (
                    self.evidence.directional_response_flow
                ),
                "directional_event_return_bps": (
                    self.evidence.directional_event_return_bps
                ),
                "directional_response_return_bps": (
                    self.evidence.directional_response_return_bps
                ),
                "event_efficiency": self.evidence.event_efficiency,
                "response_efficiency": (
                    self.evidence.response_efficiency
                ),
                "eligible": self.eligible,
                "route": self.route.value,
                "reason": self.reason,
            },
        )
        return values


def classify_flow_transition(
    evidence: FlowTransitionEvidence,
) -> FlowTransitionDecision:
    """Classify sponsorship, delayed transmission, or decay without tuning."""
    event_flow = evidence.directional_event_flow
    response_flow = evidence.directional_response_flow
    event_return = evidence.directional_event_return_bps
    response_return = evidence.directional_response_return_bps
    if event_flow <= 0.0 or response_flow <= 0.0:
        return FlowTransitionDecision(
            False,
            FlowTransitionRoute.DECAYED_NO_TRADE,
            "ACCEPTANCE_LOST_SAME_SIDE_AGGRESSOR_FLOW",
            evidence,
        )
    if response_flow >= event_flow:
        return FlowTransitionDecision(
            True,
            FlowTransitionRoute.PERSISTENT_SPONSORSHIP,
            "SAME_SIDE_AGGRESSOR_FLOW_PERSISTED_OR_INTENSIFIED",
            evidence,
        )
    delayed = (
        evidence.response_efficiency > evidence.event_efficiency
        and response_return > event_return
        and response_return > 0.0
    )
    if delayed:
        return FlowTransitionDecision(
            True,
            FlowTransitionRoute.DELAYED_PRICE_DISCOVERY,
            "WEAK_PARENT_IMPACT_RELEASED_INTO_MORE_EFFICIENT_PRICE_DISCOVERY",
            evidence,
        )
    return FlowTransitionDecision(
        False,
        FlowTransitionRoute.DECAYED_NO_TRADE,
        "OPENING_FLOW_DECAYED_WITHOUT_EFFICIENCY_RELEASE",
        evidence,
    )


def evidence_from_router_details(
    details: Mapping[str, Any],
) -> FlowTransitionEvidence:
    """Read the frozen parent and response from event-time router evidence."""
    try:
        event = details["event"]
        response = details["response"]
        return FlowTransitionEvidence(
            direction=int(event["direction"]),
            event_open=float(event["event_open"]),
            event_high=float(event["event_high"]),
            event_low=float(event["event_low"]),
            event_close=float(event["event_close"]),
            event_flow=float(event["event_flow"]),
            response_flow=float(response["flow"]),
            response_return_bps=float(response["return_bps"]),
            response_efficiency=float(response["efficiency"]),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("event-time router details are incomplete") from exc


__all__ = [
    "FlowTransitionDecision",
    "FlowTransitionEvidence",
    "FlowTransitionRoute",
    "classify_flow_transition",
    "evidence_from_router_details",
]
