"""Causal first-10s shock / remaining-50s auction response classifier.

A quarter-hour boundary burst is only the parent event. At the completed
minute we split the minute's traded notional into the opening 10-second shock
and the strictly later 10-60 second response. The response, the closing
location versus the already-completed balance edge, and the contemporaneous
L1 book response have separate roles:

- acceptance: price remains outside, later trades continue the move, displayed
  liquidity ahead withdraws, and the book supports the move;
- failed auction: price re-enters, later trades reverse the move, displayed
  liquidity ahead refills, and the book supports the reversal;
- otherwise unresolved/no trade.

No PnL, future bars, or fitted magnitude thresholds are used.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import StrEnum
import math


class BoundaryResponseDecision(StrEnum):
    ACCEPTANCE = "ACCEPTANCE"
    FAILED_AUCTION = "FAILED_AUCTION"
    UNRESOLVED = "UNRESOLVED"


@dataclass(frozen=True, slots=True)
class BoundaryResponse:
    direction: int
    boundary_level: float
    opening_close: float
    minute_close: float
    minute_notional: float
    minute_flow: float
    opening_notional: float
    opening_signed_notional: float
    depth_imbalance_1: float
    liquidity_ahead_change_1m: float

    def __post_init__(self) -> None:
        if self.direction not in (-1, 1):
            raise ValueError("direction must be -1 or +1")
        positive = (
            self.boundary_level,
            self.opening_close,
            self.minute_close,
            self.minute_notional,
            self.opening_notional,
        )
        if any(not math.isfinite(value) or value <= 0.0 for value in positive):
            raise ValueError("prices and notionals must be finite and positive")
        finite = (
            self.minute_flow,
            self.opening_signed_notional,
            self.depth_imbalance_1,
            self.liquidity_ahead_change_1m,
        )
        if any(not math.isfinite(value) for value in finite):
            raise ValueError("response inputs must be finite")
        if abs(self.minute_flow) > 1.0 + 1e-12:
            raise ValueError("minute_flow must be normalized to [-1, 1]")
        if abs(self.opening_signed_notional) > self.opening_notional + 1e-6:
            raise ValueError("opening signed notional exceeds total opening notional")

    @property
    def response_notional(self) -> float:
        return self.minute_notional - self.opening_notional

    @property
    def response_signed_notional(self) -> float:
        return self.minute_notional * self.minute_flow - self.opening_signed_notional

    @property
    def response_flow(self) -> float:
        if self.response_notional <= 0.0:
            return math.nan
        return self.response_signed_notional / self.response_notional

    @property
    def directional_response_flow(self) -> float:
        return self.direction * self.response_flow

    @property
    def directional_response_return(self) -> float:
        return self.direction * (self.minute_close - self.opening_close)

    @property
    def directional_outside_close(self) -> float:
        return self.direction * (self.minute_close - self.boundary_level)

    @property
    def directional_book_support(self) -> float:
        return self.direction * self.depth_imbalance_1


def classify_boundary_response(
    response: BoundaryResponse,
) -> tuple[BoundaryResponseDecision, str, dict[str, float | int | str]]:
    """Classify a completed minute using only sign-coherent causal evidence."""
    details: dict[str, float | int | str] = {
        **asdict(response),
        "response_notional": response.response_notional,
        "response_signed_notional": response.response_signed_notional,
        "response_flow": response.response_flow,
        "directional_response_flow": response.directional_response_flow,
        "directional_response_return": response.directional_response_return,
        "directional_outside_close": response.directional_outside_close,
        "directional_book_support": response.directional_book_support,
        "response_window": "strictly after first 10 seconds through completed minute",
    }
    if response.response_notional <= 0.0 or not math.isfinite(response.response_flow):
        return (
            BoundaryResponseDecision.UNRESOLVED,
            "NO_STRICTLY_LATER_TRADED_RESPONSE",
            details,
        )

    accepted = (
        response.directional_outside_close > 0.0
        and response.directional_response_return > 0.0
        and response.directional_response_flow > 0.0
        and response.directional_book_support > 0.0
        and response.liquidity_ahead_change_1m < 0.0
    )
    if accepted:
        return (
            BoundaryResponseDecision.ACCEPTANCE,
            "OPENING_SHOCK_TRANSMITTED_THROUGH_LATER_FLOW_PRICE_AND_BOOK",
            details,
        )

    failed = (
        response.directional_outside_close <= 0.0
        and response.directional_response_return < 0.0
        and response.directional_response_flow < 0.0
        and response.directional_book_support < 0.0
        and response.liquidity_ahead_change_1m >= 0.0
    )
    if failed:
        return (
            BoundaryResponseDecision.FAILED_AUCTION,
            "OPENING_SHOCK_REVERSED_BY_LATER_FLOW_PRICE_AND_BOOK_REFILL",
            details,
        )

    return (
        BoundaryResponseDecision.UNRESOLVED,
        "OPENING_SHOCK_RESPONSE_REMAINED_MIXED",
        details,
    )


__all__ = [
    "BoundaryResponse",
    "BoundaryResponseDecision",
    "classify_boundary_response",
]
