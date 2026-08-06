"""Pure predicates for persistent queue pressure and confirmed auction release."""
from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Iterable

from depth_logic import DIRECTIONAL_DEPTH_MIN


TWO_TO_ONE_IMBALANCE = 1.0 / 3.0


@dataclass(frozen=True, slots=True)
class CompressionRange:
    lower: float
    upper: float
    midpoint: float


def persistent_queue_pressure(
    depth_imbalances: Iterable[float],
    *,
    minimum_directional_imbalance: float = TWO_TO_ONE_IMBALANCE,
) -> int:
    """Return +1/-1 only when every completed observation agrees."""
    values = [float(value) for value in depth_imbalances]
    if not values or not all(math.isfinite(value) for value in values):
        return 0
    if minimum_directional_imbalance < 0.0:
        raise ValueError("minimum_directional_imbalance must be non-negative")
    if all(value >= minimum_directional_imbalance for value in values):
        return 1
    if all(value <= -minimum_directional_imbalance for value in values):
        return -1
    return 0


def compressed_range(
    *,
    highs: Iterable[float],
    lows: Iterable[float],
    atr: float,
    maximum_width_atr: float,
) -> CompressionRange | None:
    """Return a completed narrow range or ``None``.

    The caller supplies the width limit from an existing Candidate 05 contract;
    this function introduces no fitted threshold.
    """
    high_values = [float(value) for value in highs]
    low_values = [float(value) for value in lows]
    if not high_values or len(high_values) != len(low_values):
        return None
    values = [*high_values, *low_values, float(atr), float(maximum_width_atr)]
    if not all(math.isfinite(value) for value in values):
        return None
    if atr <= 0.0 or maximum_width_atr <= 0.0:
        return None
    upper = max(high_values)
    lower = min(low_values)
    if upper <= lower or upper - lower > maximum_width_atr * atr:
        return None
    return CompressionRange(lower=lower, upper=upper, midpoint=(lower + upper) / 2.0)


def pressure_release_breakout(
    *,
    side: int,
    compression: CompressionRange,
    open_price: float,
    high: float,
    low: float,
    close: float,
    atr: float,
    flow_60s: float,
    efficiency_60s: float,
    notional_burst: float,
    bid_depth_change_1m: float,
    ask_depth_change_1m: float,
    minimum_break_distance_atr: float,
    minimum_flow: float,
    minimum_efficiency: float,
    minimum_notional_burst: float,
    minimum_depth_withdrawal: float,
    minimum_close_location: float,
) -> bool:
    """Whether latent pressure became actual flow and efficient price discovery."""
    if side not in (-1, 1):
        raise ValueError("side must be -1 or 1")
    values = (
        compression.lower,
        compression.upper,
        open_price,
        high,
        low,
        close,
        atr,
        flow_60s,
        efficiency_60s,
        notional_burst,
        bid_depth_change_1m,
        ask_depth_change_1m,
        minimum_break_distance_atr,
        minimum_flow,
        minimum_efficiency,
        minimum_notional_burst,
        minimum_depth_withdrawal,
        minimum_close_location,
    )
    if not all(math.isfinite(float(value)) for value in values):
        return False
    if high <= low or atr <= 0.0:
        return False
    if any(
        value < 0.0
        for value in (
            minimum_break_distance_atr,
            minimum_flow,
            minimum_efficiency,
            minimum_notional_burst,
            minimum_depth_withdrawal,
            minimum_close_location,
        )
    ):
        return False
    span = high - low
    close_location = (close - low) / span if side > 0 else (high - close) / span
    if side > 0:
        broke = close >= compression.upper + minimum_break_distance_atr * atr
        opposing_depth_withdrew = ask_depth_change_1m <= -minimum_depth_withdrawal
    else:
        broke = close <= compression.lower - minimum_break_distance_atr * atr
        opposing_depth_withdrew = bid_depth_change_1m <= -minimum_depth_withdrawal
    return (
        broke
        and side * flow_60s >= minimum_flow
        and efficiency_60s >= minimum_efficiency
        and notional_burst >= minimum_notional_burst
        and opposing_depth_withdrew
        and close_location >= minimum_close_location
    )


def boundary_retest_invalidated(
    *,
    side: int,
    compression: CompressionRange,
    close: float,
) -> bool:
    """Whether price closed through the opposite side of the frozen balance."""
    if side not in (-1, 1):
        raise ValueError("side must be -1 or 1")
    if not math.isfinite(close):
        return True
    return close <= compression.lower if side > 0 else close >= compression.upper


def first_boundary_retest_response(
    *,
    side: int,
    boundary: float,
    high: float,
    low: float,
    close: float,
    flow_15s: float,
    depth_imbalance: float,
    maximum_counterflow: float,
    minimum_directional_depth: float = DIRECTIONAL_DEPTH_MIN,
) -> bool:
    """Whether the first later boundary touch was defended after completion."""
    if side not in (-1, 1):
        raise ValueError("side must be -1 or 1")
    values = (
        boundary,
        high,
        low,
        close,
        flow_15s,
        depth_imbalance,
        maximum_counterflow,
        minimum_directional_depth,
    )
    if not all(math.isfinite(float(value)) for value in values):
        return False
    if high < low or maximum_counterflow < 0.0 or minimum_directional_depth < 0.0:
        return False
    touched = low <= boundary <= high
    defended = close > boundary if side > 0 else close < boundary
    return (
        touched
        and defended
        and side * flow_15s >= -maximum_counterflow
        and side * depth_imbalance >= minimum_directional_depth
    )


def compression_structural_stop(
    *,
    side: int,
    compression: CompressionRange,
    atr: float,
    stop_buffer_atr: float,
) -> float:
    """Place invalidation beyond the opposite side of the frozen balance."""
    if side not in (-1, 1):
        raise ValueError("side must be -1 or 1")
    values = (compression.lower, compression.upper, atr, stop_buffer_atr)
    if not all(math.isfinite(float(value)) for value in values):
        return float("nan")
    if atr <= 0.0 or stop_buffer_atr < 0.0:
        return float("nan")
    buffer = atr * stop_buffer_atr
    return compression.lower - buffer if side > 0 else compression.upper + buffer


__all__ = [
    "CompressionRange",
    "TWO_TO_ONE_IMBALANCE",
    "boundary_retest_invalidated",
    "compressed_range",
    "compression_structural_stop",
    "first_boundary_retest_response",
    "persistent_queue_pressure",
    "pressure_release_breakout",
]
