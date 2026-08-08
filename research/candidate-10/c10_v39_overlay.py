"""Pure helpers for v39 entry-auction acceptance.

The v36 CE-rejection entry, source-equilibrium target, initial invalidation,
execution costs, and risk sizing are frozen.  This layer observes the first
completed minute containing or following the real passive parent fill.  The
entry auction owns risk only when that completed bar closes on the predicted
side of the pre-existing passive entry boundary.  Otherwise the retest failed
to hold the displacement side and the position is closed; no percentage,
volatility multiple, MFE, or elapsed-time threshold is introduced.
"""
from __future__ import annotations

from dataclasses import dataclass
import os

from c10_v38_overlay import (  # re-export frozen lower layers
    CostAwareRiskSizer,
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
    rejection_displacement,
    repair_kline_flow_frame,
    source_equilibrium,
)


@dataclass(frozen=True, slots=True)
class EntryAuctionAcceptance:
    accepted: bool
    direction: str
    boundary: float
    completed_close: float
    distance_from_boundary: float


def entry_auction_acceptance_enabled() -> bool:
    return os.environ.get("C10_V39_ENTRY_AUCTION_ACCEPTANCE", "0") == "1"


def evaluate_entry_auction(
    *,
    direction: str,
    completed_close: float,
    entry_boundary: float,
) -> EntryAuctionAcceptance:
    if direction == "LONG":
        distance = completed_close - entry_boundary
        accepted = distance >= 0.0
    elif direction == "SHORT":
        distance = entry_boundary - completed_close
        accepted = distance >= 0.0
    else:
        raise ValueError(f"unsupported direction: {direction}")
    return EntryAuctionAcceptance(
        accepted=accepted,
        direction=direction,
        boundary=entry_boundary,
        completed_close=completed_close,
        distance_from_boundary=distance,
    )


__all__ = [
    "CostAwareRiskSizer",
    "EntryAuctionAcceptance",
    "InternalPivotProtection",
    "LiveImpactLedger",
    "apply_cost_overlay",
    "build_leadership_gate",
    "certify_plan",
    "consequent_encroachment",
    "entry_auction_acceptance_enabled",
    "evaluate_entry_auction",
    "first_favorable_internal_pivot",
    "internal_pivot_protection_enabled",
    "micro_pivot_protection_enabled",
    "micro_pivot_reference_contract",
    "normalize_kline_open_time",
    "rejection_displacement",
    "repair_kline_flow_frame",
    "source_equilibrium",
]
