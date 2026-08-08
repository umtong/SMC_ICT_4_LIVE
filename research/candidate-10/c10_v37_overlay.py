"""Pure helpers for v37 confirmed-internal-pivot risk transfer.

The v36 entry and source-equilibrium primary target remain frozen.  This layer
changes only post-entry risk ownership: after the position exists, the first
causally confirmed five-minute higher low for a long or lower high for a short
may replace the original invalidation, provided the new stop is still live and
strictly reduces risk.  No MFE, fixed-R, elapsed-time, or fitted threshold is
introduced.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR
import os
from typing import Iterable

from c10_v36_overlay import (  # re-export frozen v36/v29/v28/v27 layers
    CostAwareRiskSizer,
    LiveImpactLedger,
    apply_cost_overlay,
    build_leadership_gate,
    certify_plan,
    consequent_encroachment,
    normalize_kline_open_time,
    rejection_displacement,
    repair_kline_flow_frame,
    source_equilibrium,
)


@dataclass(frozen=True, slots=True)
class InternalPivotProtection:
    direction: str
    pivot_event_ts_ns: int
    pivot_known_ts_ns: int
    pivot_level: float
    reference_extreme: float
    protective_stop: float
    original_stop: float
    current_price: float
    target_price: float


def internal_pivot_protection_enabled() -> bool:
    return os.environ.get("C10_V37_INTERNAL_PIVOT_PROTECTION", "0") == "1"


def _quantize_stop(raw: float, tick_size: float, direction: str) -> float:
    tick = Decimal(str(tick_size))
    if tick <= 0:
        raise ValueError("tick_size must be positive")
    value = Decimal(str(raw)) / tick
    if direction == "LONG":
        units = value.to_integral_value(rounding=ROUND_FLOOR)
    elif direction == "SHORT":
        units = value.to_integral_value(rounding=ROUND_CEILING)
    else:
        raise ValueError(f"unsupported direction: {direction}")
    return float(units * tick)


def first_favorable_internal_pivot(
    *,
    direction: str,
    internal_highs: Iterable[tuple[int, int, float]],
    internal_lows: Iterable[tuple[int, int, float]],
    entry_fill_ts_ns: int,
    observed_ts_ns: int,
    original_stop: float,
    reference_extreme: float,
    current_price: float,
    target_price: float,
    atr: float,
    stop_buffer_atr: float,
    tick_size: float,
) -> InternalPivotProtection | None:
    """Return the first live post-entry higher-low/lower-high protection.

    Pivot tuples are ``(event_ts_ns, known_ts_ns, level)``.  The pivot itself and
    its right-wing confirmation must both occur after the real entry fill.  A
    candidate is eligible only if it forms a higher low or lower high versus
    the actual CE-retest extreme which owns the initial stop, the buffered stop
    improves that original invalidation, and the completed current price remains
    between stop and target when the replacement order is submitted.
    """

    if entry_fill_ts_ns <= 0 or observed_ts_ns < entry_fill_ts_ns:
        return None
    if atr <= 0.0 or stop_buffer_atr < 0.0 or tick_size <= 0.0:
        raise ValueError(
            "ATR and tick size must be positive; buffer must be nonnegative",
        )
    if direction not in {"LONG", "SHORT"}:
        raise ValueError(f"unsupported direction: {direction}")

    raw_points = internal_lows if direction == "LONG" else internal_highs
    points = sorted(
        (
            (int(event_ts), int(known_ts), float(level))
            for event_ts, known_ts, level in raw_points
            if int(known_ts) <= observed_ts_ns
        ),
        key=lambda item: (item[1], item[0]),
    )

    buffer = stop_buffer_atr * atr
    for event_ts, known_ts, level in points:
        if event_ts <= entry_fill_ts_ns or known_ts <= entry_fill_ts_ns:
            continue
        favorable = (
            level > reference_extreme
            if direction == "LONG"
            else level < reference_extreme
        )
        if not favorable:
            continue
        raw_stop = level - buffer if direction == "LONG" else level + buffer
        stop = _quantize_stop(raw_stop, tick_size, direction)
        if direction == "LONG":
            improves = stop > original_stop
            live_order = original_stop < stop < current_price < target_price
        else:
            improves = stop < original_stop
            live_order = target_price < current_price < stop < original_stop
        if not improves or not live_order:
            continue
        return InternalPivotProtection(
            direction=direction,
            pivot_event_ts_ns=event_ts,
            pivot_known_ts_ns=known_ts,
            pivot_level=level,
            reference_extreme=reference_extreme,
            protective_stop=stop,
            original_stop=original_stop,
            current_price=current_price,
            target_price=target_price,
        )
    return None


__all__ = [
    "CostAwareRiskSizer",
    "InternalPivotProtection",
    "LiveImpactLedger",
    "apply_cost_overlay",
    "build_leadership_gate",
    "certify_plan",
    "consequent_encroachment",
    "first_favorable_internal_pivot",
    "internal_pivot_protection_enabled",
    "normalize_kline_open_time",
    "rejection_displacement",
    "repair_kline_flow_frame",
    "source_equilibrium",
]
