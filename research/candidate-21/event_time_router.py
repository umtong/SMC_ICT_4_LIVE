"""Pure causal router for quarter-hour opening events on 10-second bars.

The parent is the first completed 10-second interval of a UTC quarter-hour.
It may attack one edge of the strictly prior 15-minute balance.  The parent is
never an entry.  Only the immediately following, non-overlapping 10-second
interval can resolve the event:

* acceptance: the response holds outside the edge and transmits same-side
  price and aggressor flow;
* failed auction: the response re-enters the balance and transmits opposite
  price and aggressor flow;
* otherwise unresolved/no trade.

The router contains no execution, accounting, PnL or future information.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import StrEnum
import math
from typing import Any


class EventDecision(StrEnum):
    WAITING = "WAITING"
    ACCEPTANCE = "ACCEPTANCE"
    FAILED_AUCTION = "FAILED_AUCTION"
    INVALIDATED = "INVALIDATED"
    UNRESOLVED = "UNRESOLVED"


def _finite(value: float) -> bool:
    return math.isfinite(float(value))


def directional_close_location(
    *,
    direction: int,
    high: float,
    low: float,
    close: float,
) -> float:
    """Return close location in ``direction``, bounded to [0, 1]."""
    if direction not in (-1, 1):
        raise ValueError("direction must be -1 or +1")
    if not (_finite(high) and _finite(low) and _finite(close)):
        raise ValueError("OHLC values must be finite")
    if low > high or not low <= close <= high:
        raise ValueError("OHLC values are inconsistent")
    span = max(high - low, 1e-12)
    raw = (close - low) / span if direction > 0 else (high - close) / span
    return min(1.0, max(0.0, raw))


@dataclass(frozen=True, slots=True)
class TenSecondEvent:
    scenario_id: str
    direction: int
    event_index: int
    boundary_level: float
    range_opposite: float
    acceptance_target: float
    rejection_target: float
    atr: float
    event_open: float
    event_high: float
    event_low: float
    event_close: float
    event_flow: float
    event_notional: float
    phase_burst: float
    decision: EventDecision = EventDecision.WAITING
    reason: str = "OPENING_EVENT_AWAITS_IMMEDIATE_RESPONSE"

    def __post_init__(self) -> None:
        if self.direction not in (-1, 1):
            raise ValueError("direction must be -1 or +1")
        finite_positive = (
            self.boundary_level,
            self.range_opposite,
            self.acceptance_target,
            self.rejection_target,
            self.atr,
            self.event_open,
            self.event_high,
            self.event_low,
            self.event_close,
            self.event_notional,
            self.phase_burst,
        )
        if any(not _finite(value) or value <= 0.0 for value in finite_positive):
            raise ValueError("event prices, ATR, notional and burst must be positive")
        if not _finite(self.event_flow) or abs(self.event_flow) > 1.0 + 1e-12:
            raise ValueError("event_flow must be finite and normalized")
        if self.event_low > self.event_high:
            raise ValueError("event range is invalid")
        if not self.event_low <= self.event_open <= self.event_high:
            raise ValueError("event open lies outside event range")
        if not self.event_low <= self.event_close <= self.event_high:
            raise ValueError("event close lies outside event range")
        if self.direction > 0:
            if not self.range_opposite < self.boundary_level < self.acceptance_target:
                raise ValueError("long event balance geometry is invalid")
        else:
            if not self.acceptance_target < self.boundary_level < self.range_opposite:
                raise ValueError("short event balance geometry is invalid")
        if self.rejection_target != self.range_opposite:
            raise ValueError("rejection target must equal the opposite balance edge")


@dataclass(frozen=True, slots=True)
class TenSecondResponse:
    bar_index: int
    open: float
    high: float
    low: float
    close: float
    flow: float
    return_bps: float
    efficiency: float

    def __post_init__(self) -> None:
        values = (
            self.open,
            self.high,
            self.low,
            self.close,
            self.flow,
            self.return_bps,
            self.efficiency,
        )
        if any(not _finite(value) for value in values):
            raise ValueError("response values must be finite")
        if self.low > self.high:
            raise ValueError("response range is invalid")
        if not self.low <= self.open <= self.high:
            raise ValueError("response open lies outside range")
        if not self.low <= self.close <= self.high:
            raise ValueError("response close lies outside range")
        if abs(self.flow) > 1.0 + 1e-12:
            raise ValueError("response flow must be normalized")
        if not 0.0 <= self.efficiency <= 1.0 + 1e-12:
            raise ValueError("response efficiency must be in [0, 1]")


def classify_response(
    event: TenSecondEvent,
    response: TenSecondResponse,
) -> tuple[EventDecision, str, dict[str, Any]]:
    """Resolve an event from exactly its first non-overlapping response bar."""
    if event.decision is not EventDecision.WAITING:
        raise ValueError("terminal event cannot be classified again")
    if response.bar_index != event.event_index + 1:
        raise ValueError("only the immediate next 10-second bar may respond")

    direction = event.direction
    outside = direction * (response.close - event.boundary_level)
    extension = direction * (response.close - event.event_close)
    directional_flow = direction * response.flow
    directional_return = direction * response.return_bps
    directional_location = directional_close_location(
        direction=direction,
        high=response.high,
        low=response.low,
        close=response.close,
    )
    reverse_location = directional_close_location(
        direction=-direction,
        high=response.high,
        low=response.low,
        close=response.close,
    )
    acceptance_touched = (
        response.high >= event.acceptance_target
        if direction > 0
        else response.low <= event.acceptance_target
    )
    rejection_touched = (
        response.low <= event.rejection_target
        if direction > 0
        else response.high >= event.rejection_target
    )
    details: dict[str, Any] = {
        "event": {
            **asdict(event),
            "decision": event.decision.value,
        },
        "response": asdict(response),
        "directional_outside": outside,
        "directional_extension_from_event_close": extension,
        "directional_response_flow": directional_flow,
        "directional_response_return_bps": directional_return,
        "directional_close_location": directional_location,
        "reverse_close_location": reverse_location,
        "acceptance_target_touched": acceptance_touched,
        "rejection_target_touched": rejection_touched,
        "response_window": "immediate non-overlapping 10-second interval",
    }

    if acceptance_touched or rejection_touched:
        return (
            EventDecision.INVALIDATED,
            "NATURAL_TARGET_CONSUMED_BEFORE_ENTRY",
            details,
        )

    accepted = (
        outside > 0.0
        and extension > 0.0
        and directional_flow > 0.0
        and directional_return > 0.0
        and directional_location >= 0.5
    )
    if accepted:
        return (
            EventDecision.ACCEPTANCE,
            "IMMEDIATE_RESPONSE_HELD_OUTSIDE_AND_TRANSMITTED",
            details,
        )

    failed = (
        outside <= 0.0
        and extension < 0.0
        and directional_flow < 0.0
        and directional_return < 0.0
        and reverse_location >= 0.5
    )
    if failed:
        return (
            EventDecision.FAILED_AUCTION,
            "IMMEDIATE_RESPONSE_REENTERED_AND_REVERSED",
            details,
        )

    return (
        EventDecision.UNRESOLVED,
        "IMMEDIATE_RESPONSE_REMAINED_MIXED",
        details,
    )


__all__ = [
    "EventDecision",
    "TenSecondEvent",
    "TenSecondResponse",
    "classify_response",
    "directional_close_location",
]
