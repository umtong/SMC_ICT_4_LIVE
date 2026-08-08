"""Pure causal context for synchronized quarter-hour flow carry.

A 10-second acceptance event is eligible only when it occurs at a full UTC hour
and the completed response close is already aligned with both one-hour and
three-hour price discovery.  The stop belongs to the same medium-horizon leg:
the strictly prior one-hour opposite extreme plus a 30-minute ATR buffer.

The module contains no NautilusTrader dependency, execution, PnL or future data.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import math
from typing import Mapping, Sequence


SECOND_NS = 1_000_000_000
MINUTE_NS = 60 * SECOND_NS
HOUR_NS = 60 * MINUTE_NS


@dataclass(frozen=True, slots=True)
class FlowCarryPlan:
    side: int
    response_ts_ns: int
    entry_estimate: float
    close_1h_ago: float
    close_3h_ago: float
    directional_return_1h: float
    directional_return_3h: float
    prior_hour_high: float
    prior_hour_low: float
    atr_30m: float
    stop_price: float
    hold_until_ns: int


@dataclass(frozen=True, slots=True)
class FlowCarryDecision:
    eligible: bool
    reason: str
    plan: FlowCarryPlan | None = None


def _number(row: Mapping[str, float | int], name: str) -> float:
    value = float(row[name])
    if not math.isfinite(value):
        raise ValueError(f"{name} must be finite")
    return value


def _validate_rows(rows: Sequence[Mapping[str, float | int]]) -> None:
    if not rows:
        raise ValueError("rows cannot be empty")
    timestamps = [int(row["ts"]) for row in rows]
    if any(left >= right for left, right in zip(timestamps, timestamps[1:])):
        raise ValueError("rows must be strictly time ordered")
    for row in rows:
        high = _number(row, "high")
        low = _number(row, "low")
        open_price = _number(row, "open")
        close = _number(row, "close")
        if low > high or not low <= open_price <= high or not low <= close <= high:
            raise ValueError("row OHLC values are inconsistent")
        if low <= 0.0:
            raise ValueError("prices must be positive")


def _last_at_or_before(
    rows: Sequence[Mapping[str, float | int]],
    target_ts_ns: int,
) -> Mapping[str, float | int] | None:
    for row in reversed(rows):
        if int(row["ts"]) <= target_ts_ns:
            return row
    return None


def _atr_before(
    rows: Sequence[Mapping[str, float | int]],
    *,
    current_ts_ns: int,
    window_ns: int,
) -> float:
    prior = [row for row in rows if int(row["ts"]) < current_ts_ns]
    if len(prior) < 2:
        return float("nan")
    start_ns = current_ts_ns - window_ns
    values: list[float] = []
    for previous, current in zip(prior, prior[1:]):
        if int(current["ts"]) < start_ns:
            continue
        previous_close = _number(previous, "close")
        high = _number(current, "high")
        low = _number(current, "low")
        values.append(
            max(
                high - low,
                abs(high - previous_close),
                abs(low - previous_close),
            ),
        )
    if not values:
        return float("nan")
    return sum(values) / len(values)


def is_full_utc_hour(ts_ns: int) -> bool:
    moment = datetime.fromtimestamp(ts_ns / SECOND_NS, tz=timezone.utc)
    return moment.minute == 0


def build_flow_carry_plan(
    rows: Sequence[Mapping[str, float | int]],
    *,
    side: int,
    stop_buffer_atr: float = 0.08,
    hold_seconds: int = 4 * 60 * 60,
    require_full_hour: bool = True,
) -> FlowCarryDecision:
    """Build a synchronized medium-horizon plan from completed observations."""
    if side not in (-1, 1):
        raise ValueError("side must be -1 or +1")
    if not math.isfinite(stop_buffer_atr) or stop_buffer_atr < 0.0:
        raise ValueError("stop_buffer_atr must be finite and nonnegative")
    if hold_seconds < 1:
        raise ValueError("hold_seconds must be positive")
    _validate_rows(rows)

    current = rows[-1]
    current_ts = int(current["ts"])
    entry = _number(current, "close")
    if require_full_hour and not is_full_utc_hour(current_ts):
        return FlowCarryDecision(False, "NOT_FULL_UTC_HOUR")

    prior = [row for row in rows[:-1] if int(row["ts"]) < current_ts]
    one_hour_row = _last_at_or_before(prior, current_ts - HOUR_NS)
    three_hour_row = _last_at_or_before(prior, current_ts - 3 * HOUR_NS)
    if one_hour_row is None or three_hour_row is None:
        return FlowCarryDecision(False, "INSUFFICIENT_ONE_OR_THREE_HOUR_CONTEXT")

    close_1h = _number(one_hour_row, "close")
    close_3h = _number(three_hour_row, "close")
    directional_1h = side * (entry / close_1h - 1.0)
    directional_3h = side * (entry / close_3h - 1.0)
    if directional_1h <= 0.0 or directional_3h <= 0.0:
        return FlowCarryDecision(False, "ONE_AND_THREE_HOUR_TREND_NOT_ALIGNED")

    prior_hour = [
        row
        for row in prior
        if int(row["ts"]) >= current_ts - HOUR_NS
    ]
    if len(prior_hour) < 2:
        return FlowCarryDecision(False, "INSUFFICIENT_PRIOR_HOUR_STRUCTURE")
    prior_high = max(_number(row, "high") for row in prior_hour)
    prior_low = min(_number(row, "low") for row in prior_hour)
    atr_30m = _atr_before(
        rows,
        current_ts_ns=current_ts,
        window_ns=30 * MINUTE_NS,
    )
    if not math.isfinite(atr_30m) or atr_30m <= 0.0:
        return FlowCarryDecision(False, "INVALID_PRIOR_30M_ATR")

    buffer = stop_buffer_atr * atr_30m
    stop = prior_low - buffer if side > 0 else prior_high + buffer
    valid_geometry = 0.0 < stop < entry if side > 0 else stop > entry > 0.0
    if not valid_geometry:
        return FlowCarryDecision(False, "STRUCTURAL_STOP_NOT_BEHIND_ENTRY")

    plan = FlowCarryPlan(
        side=side,
        response_ts_ns=current_ts,
        entry_estimate=entry,
        close_1h_ago=close_1h,
        close_3h_ago=close_3h,
        directional_return_1h=directional_1h,
        directional_return_3h=directional_3h,
        prior_hour_high=prior_high,
        prior_hour_low=prior_low,
        atr_30m=atr_30m,
        stop_price=stop,
        hold_until_ns=current_ts + hold_seconds * SECOND_NS,
    )
    return FlowCarryDecision(
        True,
        "FULL_HOUR_ACCEPTANCE_WITH_ONE_AND_THREE_HOUR_PRICE_DISCOVERY",
        plan,
    )


__all__ = [
    "FlowCarryDecision",
    "FlowCarryPlan",
    "HOUR_NS",
    "MINUTE_NS",
    "SECOND_NS",
    "build_flow_carry_plan",
    "is_full_utc_hour",
]
