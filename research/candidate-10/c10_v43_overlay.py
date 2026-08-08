"""v43 funded microstructure risk transfer over the v41 best entry.

The v40 source-range detector, v41 first-displacement near-edge entry, source
midpoint target, original raid invalidation, all-cost sizing and global slot are
frozen.  A one-minute right-confirmed pivot on the profitable side of the real
entry is evidence that the predicted auction has begun.  It does not become a
hard stop.  Once current modeled net profit can fund the complete original-stop
loss of the residual quantity, close only the minimum solved quantity and leave
the residual on its original target and invalidation.  No fixed partial fraction
or MFE/R threshold is used.
"""
from __future__ import annotations

from dataclasses import dataclass
import os
from typing import Iterable

from c10_v32_overlay import FundedReduction, solve_funded_reduction
from c10_v41_overlay import (  # re-export frozen current layers
    CostAwareRiskSizer,
    FirstDisplacementEntryDecision,
    InternalPivotProtection,
    LiveImpactLedger,
    apply_cost_overlay,
    build_leadership_gate,
    certify_plan,
    consequent_encroachment,
    first_favorable_internal_pivot,
    internal_pivot_protection_enabled,
    micro_pivot_protection_enabled,
    micro_pivot_reference_contract,
    normalize_kline_open_time,
    reframe_first_displacement_entry,
    rejection_displacement,
    repair_kline_flow_frame,
    source_entry_mode,
    source_equilibrium,
    source_equilibrium_detector_enabled,
)


@dataclass(frozen=True, slots=True)
class FavorablePivotObservation:
    direction: str
    pivot_event_ts_ns: int
    pivot_known_ts_ns: int
    pivot_level: float
    entry_reference: float
    current_price: float
    target_price: float


def funded_micro_reduction_enabled() -> bool:
    return os.environ.get("C10_V43_FUNDED_MICRO_REDUCTION", "0") == "1"


def first_favorable_pivot_observation(
    *,
    direction: str,
    micro_highs: Iterable[tuple[int, int, float]],
    micro_lows: Iterable[tuple[int, int, float]],
    entry_fill_ts_ns: int,
    observed_ts_ns: int,
    entry_reference: float,
    current_price: float,
    target_price: float,
) -> FavorablePivotObservation | None:
    """Return the first causal profitable-side pivot still relevant now.

    The pivot event and its one-right-bar confirmation must both follow the real
    fill.  The current completed price must remain between entry and target in
    the predicted direction.  Cost feasibility and reduction size are decided
    separately by ``solve_funded_reduction``; an early pivot can therefore remain
    armed until enough genuine net gain exists to fund residual risk.
    """

    if direction not in {"LONG", "SHORT"}:
        raise ValueError(f"unsupported direction: {direction}")
    if entry_fill_ts_ns <= 0 or observed_ts_ns < entry_fill_ts_ns:
        return None
    points = micro_lows if direction == "LONG" else micro_highs
    ordered = sorted(
        (
            (int(event_ts), int(known_ts), float(level))
            for event_ts, known_ts, level in points
            if int(known_ts) <= observed_ts_ns
        ),
        key=lambda item: (item[1], item[0]),
    )
    current_favorable = (
        entry_reference < current_price < target_price
        if direction == "LONG"
        else target_price < current_price < entry_reference
    )
    if not current_favorable:
        return None
    for event_ts, known_ts, level in ordered:
        if event_ts <= entry_fill_ts_ns or known_ts <= entry_fill_ts_ns:
            continue
        pivot_favorable = (
            level > entry_reference
            if direction == "LONG"
            else level < entry_reference
        )
        if not pivot_favorable:
            continue
        return FavorablePivotObservation(
            direction=direction,
            pivot_event_ts_ns=event_ts,
            pivot_known_ts_ns=known_ts,
            pivot_level=level,
            entry_reference=entry_reference,
            current_price=current_price,
            target_price=target_price,
        )
    return None


__all__ = [
    "CostAwareRiskSizer",
    "FavorablePivotObservation",
    "FirstDisplacementEntryDecision",
    "FundedReduction",
    "InternalPivotProtection",
    "LiveImpactLedger",
    "apply_cost_overlay",
    "build_leadership_gate",
    "certify_plan",
    "consequent_encroachment",
    "first_favorable_internal_pivot",
    "first_favorable_pivot_observation",
    "funded_micro_reduction_enabled",
    "internal_pivot_protection_enabled",
    "micro_pivot_protection_enabled",
    "micro_pivot_reference_contract",
    "normalize_kline_open_time",
    "reframe_first_displacement_entry",
    "rejection_displacement",
    "repair_kline_flow_frame",
    "solve_funded_reduction",
    "source_entry_mode",
    "source_equilibrium",
    "source_equilibrium_detector_enabled",
]
