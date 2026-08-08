"""Pure causal router for quarter-hour sponsored acceptance.

The router deliberately assigns different observations to different jobs:

* UTC quarter-hour is context only.
* The first ten seconds of the opening minute measure sponsored initiation.
* The completed opening minute establishes acceptance beyond a pre-event range.
* A strictly later bar must retest and defend the broken range boundary.

No function in this module submits orders, sizes risk, or computes PnL.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import math
from typing import Sequence


@dataclass(frozen=True, slots=True)
class QuarterHourContext:
    side: int
    boundary: float
    opposite_boundary: float
    opening_extreme: float
    atr: float
    opening_time_ns: int


@dataclass(frozen=True, slots=True)
class QuarterHourThresholds:
    opening_burst_min: float = 1.0
    opening_flow_min: float = 0.14
    full_flow_min: float = 0.10
    efficiency_min: float = 0.45
    displacement_atr_min: float = 0.05
    opening_close_location_min: float = 0.62
    retest_tolerance_atr: float = 0.15
    retest_close_location_min: float = 0.56


@dataclass(frozen=True, slots=True)
class RetestDecision:
    state: str
    reason: str
    touched: bool
    invalidated: bool


def is_utc_quarter_hour(ts_event_ns: int) -> bool:
    """Return true only for a completed minute which opened on :00/:15/:30/:45."""
    if ts_event_ns <= 0:
        return False
    moment = datetime.fromtimestamp(ts_event_ns / 1_000_000_000, tz=timezone.utc)
    return moment.minute % 15 == 0


def _finite(*values: float) -> bool:
    return all(math.isfinite(float(value)) for value in values)


def _directional_close_location(
    *,
    side: int,
    high: float,
    low: float,
    close: float,
) -> float:
    span = high - low
    if side not in (-1, 1) or not _finite(high, low, close) or span <= 0.0:
        return float("nan")
    location = (close - low) / span
    return location if side > 0 else 1.0 - location


def detect_opening_acceptance(
    *,
    ts_event_ns: int,
    prior_highs: Sequence[float],
    prior_lows: Sequence[float],
    opening_high: float,
    opening_low: float,
    opening_close: float,
    atr: float,
    opening_flow_10s: float,
    opening_notional_burst_10s: float,
    full_flow_60s: float,
    return_60s_bps: float,
    efficiency_60s: float,
    thresholds: QuarterHourThresholds,
) -> QuarterHourContext | None:
    """Detect a completed sponsored acceptance bar without using later data."""
    if not is_utc_quarter_hour(ts_event_ns):
        return None
    if not prior_highs or len(prior_highs) != len(prior_lows):
        return None
    values = [
        *prior_highs,
        *prior_lows,
        opening_high,
        opening_low,
        opening_close,
        atr,
        opening_flow_10s,
        opening_notional_burst_10s,
        full_flow_60s,
        return_60s_bps,
        efficiency_60s,
    ]
    if not _finite(*values) or atr <= 0.0:
        return None
    if opening_notional_burst_10s < thresholds.opening_burst_min:
        return None

    prior_high = max(float(value) for value in prior_highs)
    prior_low = min(float(value) for value in prior_lows)
    candidates: list[int] = []
    if (
        opening_close >= prior_high + thresholds.displacement_atr_min * atr
        and opening_high > prior_high
    ):
        candidates.append(1)
    if (
        opening_close <= prior_low - thresholds.displacement_atr_min * atr
        and opening_low < prior_low
    ):
        candidates.append(-1)
    if len(candidates) != 1:
        return None

    side = candidates[0]
    if side * opening_flow_10s < thresholds.opening_flow_min:
        return None
    if side * full_flow_60s < thresholds.full_flow_min:
        return None
    if side * return_60s_bps <= 0.0:
        return None
    if efficiency_60s < thresholds.efficiency_min:
        return None
    close_location = _directional_close_location(
        side=side,
        high=opening_high,
        low=opening_low,
        close=opening_close,
    )
    if (
        not math.isfinite(close_location)
        or close_location < thresholds.opening_close_location_min
    ):
        return None

    return QuarterHourContext(
        side=side,
        boundary=prior_high if side > 0 else prior_low,
        opposite_boundary=prior_low if side > 0 else prior_high,
        opening_extreme=opening_high if side > 0 else opening_low,
        atr=atr,
        opening_time_ns=ts_event_ns,
    )


def evaluate_defended_retest(
    *,
    context: QuarterHourContext,
    high: float,
    low: float,
    close: float,
    tail_flow_15s: float,
    depth_imbalance_1: float,
    thresholds: QuarterHourThresholds,
) -> RetestDecision:
    """Classify a strictly later bar as waiting, invalid, or defended retest."""
    if not _finite(high, low, close, tail_flow_15s, depth_imbalance_1):
        return RetestDecision(
            state="WAITING",
            reason="RETEST_OBSERVATION_INCOMPLETE",
            touched=False,
            invalidated=False,
        )
    side = context.side
    tolerance = thresholds.retest_tolerance_atr * context.atr
    if side > 0:
        invalidated = close < context.boundary - tolerance
        touched = low <= context.boundary + tolerance
        held = close >= context.boundary
    else:
        invalidated = close > context.boundary + tolerance
        touched = high >= context.boundary - tolerance
        held = close <= context.boundary
    if invalidated:
        return RetestDecision(
            state="INVALIDATED",
            reason="ACCEPTED_RANGE_BOUNDARY_WAS_LOST",
            touched=touched,
            invalidated=True,
        )
    if not touched:
        return RetestDecision(
            state="WAITING",
            reason="ACCEPTED_RANGE_HAS_NOT_RETESTED",
            touched=False,
            invalidated=False,
        )

    close_location = _directional_close_location(
        side=side,
        high=high,
        low=low,
        close=close,
    )
    if not held:
        return RetestDecision(
            state="WAITING",
            reason="RETEST_TOUCHED_BUT_DID_NOT_CLOSE_OUTSIDE_RANGE",
            touched=True,
            invalidated=False,
        )
    if side * tail_flow_15s < 0.0:
        return RetestDecision(
            state="WAITING",
            reason="RETEST_TAIL_FLOW_OPPOSED_ACCEPTANCE",
            touched=True,
            invalidated=False,
        )
    if side * depth_imbalance_1 < 0.0:
        return RetestDecision(
            state="WAITING",
            reason="RETEST_DISPLAYED_QUEUE_OPPOSED_ACCEPTANCE",
            touched=True,
            invalidated=False,
        )
    if (
        not math.isfinite(close_location)
        or close_location < thresholds.retest_close_location_min
    ):
        return RetestDecision(
            state="WAITING",
            reason="RETEST_CLOSE_LOCATION_DID_NOT_CONFIRM_DEFENSE",
            touched=True,
            invalidated=False,
        )
    return RetestDecision(
        state="CONFIRMED",
        reason="STRICTLY_LATER_RETEST_DEFENDED_ACCEPTED_RANGE_BOUNDARY",
        touched=True,
        invalidated=False,
    )


__all__ = [
    "QuarterHourContext",
    "QuarterHourThresholds",
    "RetestDecision",
    "detect_opening_acceptance",
    "evaluate_defended_retest",
    "is_utc_quarter_hour",
]
