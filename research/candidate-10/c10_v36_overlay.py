"""Pure causal helpers for v36 CE-retest rejection state machine."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from c10_v29_overlay import (  # re-export frozen v29/v28/v27 infrastructure
    CostAwareRiskSizer,
    LiveImpactLedger,
    apply_cost_overlay,
    build_leadership_gate,
    certify_plan,
    repair_kline_flow_frame,
)
from c10_v33_overlay import normalize_kline_open_time


@dataclass(frozen=True, slots=True)
class RejectionDisplacement:
    confirmed: bool
    structural_break: bool
    directional_flow: bool
    displacement_body: bool
    close_location: bool


def consequent_encroachment(zone_low: float, zone_high: float) -> float:
    if not zone_low < zone_high:
        raise ValueError("displacement zone must have positive width")
    return (zone_low + zone_high) / 2.0


def source_equilibrium(pool: Any) -> float:
    opposite = getattr(pool, "opposite_level", None)
    if opposite is None:
        raise ValueError("source pool has no paired endpoint")
    return (float(pool.level) + float(opposite)) / 2.0


def rejection_displacement(
    *,
    direction: str,
    bar: Any,
    touch_bar_threshold: float,
    atr: float,
    config: Any,
) -> RejectionDisplacement:
    if atr <= 0.0:
        raise ValueError("ATR must be positive")
    if direction == "LONG":
        structural = float(bar.close) > touch_bar_threshold
        flow = float(bar.signed_flow) >= float(config.displacement_flow_min)
        location = float(bar.close_location) >= float(config.acceptance_close_location)
    elif direction == "SHORT":
        structural = float(bar.close) < touch_bar_threshold
        flow = float(bar.signed_flow) <= -float(config.displacement_flow_min)
        location = float(bar.close_location) <= 1.0 - float(config.acceptance_close_location)
    else:
        raise ValueError(f"unsupported direction: {direction}")
    body = float(bar.body) >= float(config.displacement_body_atr) * atr
    return RejectionDisplacement(
        confirmed=structural and flow and body and location,
        structural_break=structural,
        directional_flow=flow,
        displacement_body=body,
        close_location=location,
    )


__all__ = [
    "CostAwareRiskSizer",
    "LiveImpactLedger",
    "RejectionDisplacement",
    "apply_cost_overlay",
    "build_leadership_gate",
    "certify_plan",
    "consequent_encroachment",
    "normalize_kline_open_time",
    "rejection_displacement",
    "repair_kline_flow_frame",
    "source_equilibrium",
]
