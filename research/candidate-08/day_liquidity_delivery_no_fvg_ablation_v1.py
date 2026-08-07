"""Single diagnostic ablation for day-liquidity-delivery V1.

Only the standard three-bar FVG non-overlap requirement is removed.  The completed H4 draw,
session route, separate five-minute frozen-swing break, displacement body/range/close-location,
first subsequent structural retest, HTF target, stop, costs, and shared-NAV risk contract remain.

This module is diagnostic-only and cannot be promoted directly.
"""

from __future__ import annotations

from dataclasses import replace
from math import isfinite
from typing import Any

import day_liquidity_delivery_signals_v1 as base
from quote_resiliency_signals import QuoteResiliencySignalBundle


SIGNAL_REVISION = "DAY_LIQUIDITY_DELIVERY_REMOVE_STANDARD_FVG_ABLATION_V1"
ABLATION_MODE = "REMOVE_STANDARD_THREE_BAR_FVG_USE_BROKEN_SWING_RETEST"
DIAGNOSTIC_ONLY = True

_REASON_RENAMES = {
    "NO_FIVE_MINUTE_MSS_FVG_BEFORE_ROUTE_EXPIRY": (
        "NO_FIVE_MINUTE_MSS_DISPLACEMENT_BEFORE_ROUTE_EXPIRY"
    ),
    "NO_FIRST_FVG_RETRACE_BEFORE_ROUTE_EXPIRY": (
        "NO_FIRST_BROKEN_SWING_RETEST_BEFORE_ROUTE_EXPIRY"
    ),
    "FIRST_FVG_TOUCH_FAILED_DELIVERY_CONFIRMATION": (
        "FIRST_BROKEN_SWING_RETEST_FAILED_DELIVERY_CONFIRMATION"
    ),
}
_DIAGNOSTIC_RENAMES = {
    "FIVE_MINUTE_MSS_FVG_CONFIRMED": (
        "FIVE_MINUTE_MSS_DISPLACEMENT_CONFIRMED_NO_FVG_REQUIRED"
    ),
    "FIRST_FVG_RETRACE_HELD": "FIRST_BROKEN_SWING_RETEST_HELD",
    **_REASON_RENAMES,
}


def _five_displacement_broken_swing_retest(
    *,
    bars,
    position: int,
    direction: int,
    frozen_swing,
    prior_body_median,
    prior_range_median,
    close_location: float,
    tick: float,
):
    """Keep displacement/MSS, collapse the delivery zone to the broken swing price."""

    if position < 2 or direction not in (-1, 1):
        return None
    bar = bars[position]
    if not base._finite_five_bar(bar):
        return None
    body_reference = float(prior_body_median[position])
    range_reference = float(prior_range_median[position])
    if (
        not isfinite(body_reference)
        or not isfinite(range_reference)
        or body_reference <= 0.0
        or range_reference <= 0.0
    ):
        return None
    directional_body = direction * (float(bar.close) - float(bar.open))
    bar_range = float(bar.high) - float(bar.low)
    broke = (
        float(bar.close) > float(frozen_swing.price) + tick
        if direction > 0
        else float(bar.close) < float(frozen_swing.price) - tick
    )
    if not (
        broke
        and directional_body >= body_reference
        and bar_range >= range_reference
        and base._draw_close_location(bar, direction) >= close_location
    ):
        return None
    structural_level = float(frozen_swing.price)
    return base._Displacement(
        position=position,
        time_ns=int(bar.ts_event_ns),
        fvg_low=structural_level,
        fvg_high=structural_level,
        broken_swing=frozen_swing,
    )


def _renamed_event(event):
    if event.event_type == "FIVE_MINUTE_MSS_DISPLACEMENT_FVG_CONFIRMED":
        return replace(
            event,
            event_type="FIVE_MINUTE_MSS_DISPLACEMENT_CONFIRMED_NO_FVG_REQUIRED",
            reason_code="FROZEN_OPPOSING_SWING_BROKEN_WITH_DISPLACEMENT",
            details={
                **event.details,
                "ablation_mode": ABLATION_MODE,
                "delivery_zone_mode": "BROKEN_SWING_RETEST",
            },
        )
    if event.event_type == "FIRST_FIVE_MINUTE_FVG_RETRACE_DELIVERY_CONFIRMED":
        return replace(
            event,
            event_type="FIRST_BROKEN_SWING_RETEST_DELIVERY_CONFIRMED",
            reason_code="FIRST_SUBSEQUENT_BROKEN_SWING_RETEST_HELD_AND_CLOSED_WITH_HTF_DRAW",
            details={
                **event.details,
                "ablation_mode": ABLATION_MODE,
                "delivery_zone_mode": "BROKEN_SWING_RETEST",
            },
        )
    return event


def _renamed_rejection(raw: dict[str, Any]) -> dict[str, Any]:
    reason = str(raw.get("reason"))
    mapped = _REASON_RENAMES.get(reason, reason)
    details = dict(raw.get("details", {}))
    details.update(
        {
            "ablation_mode": ABLATION_MODE,
            "diagnostic_only": True,
        }
    )
    return {**raw, "reason": mapped, "details": details}


def build_day_liquidity_delivery_no_fvg_ablation_signals(**kwargs) -> QuoteResiliencySignalBundle:
    """Run the same detector with exactly one variable removed."""

    original = base._five_displacement_fvg
    base._five_displacement_fvg = _five_displacement_broken_swing_retest
    try:
        bundle = base.build_day_liquidity_delivery_signals(**kwargs)
    finally:
        base._five_displacement_fvg = original

    transformed = {}
    for timestamp, signals in bundle.signals_by_time_ns.items():
        transformed[timestamp] = tuple(
            replace(
                signal,
                events=tuple(_renamed_event(event) for event in signal.events),
                details={
                    **signal.details,
                    "signal_revision": SIGNAL_REVISION,
                    "ablation_mode": ABLATION_MODE,
                    "diagnostic_only": True,
                    "delivery_zone_mode": "BROKEN_SWING_RETEST",
                },
            )
            for signal in signals
        )

    diagnostics: dict[str, int] = {}
    for key, value in bundle.diagnostics.items():
        mapped = _DIAGNOSTIC_RENAMES.get(key, key)
        diagnostics[mapped] = diagnostics.get(mapped, 0) + int(value)
    diagnostics["ABLATION_REMOVE_STANDARD_FVG"] = 1

    return QuoteResiliencySignalBundle(
        signals_by_time_ns=transformed,
        diagnostics=dict(sorted(diagnostics.items())),
        rejected_scenarios=tuple(_renamed_rejection(raw) for raw in bundle.rejected_scenarios),
    )


__all__ = [
    "ABLATION_MODE",
    "DIAGNOSTIC_ONLY",
    "SIGNAL_REVISION",
    "build_day_liquidity_delivery_no_fvg_ablation_signals",
]
