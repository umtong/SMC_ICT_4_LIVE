"""v46 completed-bar entry-leg failure over the frozen hard raid stop.

The v40 source failed-auction detector, v41 first-displacement near-edge entry,
v44 source-equilibrium primary target, original source-raid hard stop, all-cost
sizing and global portfolio slot are frozen.  v45 showed that replacing the
hard stop with the first displacement void boundary creates an execution and
risk-ownership mismatch: the stop can already be inside the spread when the
passive parent fills, and narrow nominal risk expands quantity and impact.

v46 therefore keeps the original hard stop for catastrophe protection and the
3% loss budget.  It adds one state transition only: after a real fill, a
completed one-minute close through the opposite edge of the first causal
confirmation displacement void invalidates the entry leg and submits a market
exit.  Intrabar touches do not qualify.  No buffer, distance, consecutive-close,
MFE, time, percentile or fitted threshold is introduced.
"""
from __future__ import annotations

from dataclasses import dataclass
import os

from c10_v45_overlay import (  # re-export frozen lower layers
    CostAwareRiskSizer,
    EntryLegInvalidationDecision,
    FirstDisplacementEntryDecision,
    InternalLiquidityCandidate,
    InternalPivotProtection,
    LiveImpactLedger,
    TargetHierarchyDecision,
    apply_cost_overlay,
    build_leadership_gate,
    certify_plan,
    consequent_encroachment,
    first_favorable_internal_pivot,
    internal_pivot_protection_enabled,
    invalidation_mode,
    micro_pivot_protection_enabled,
    micro_pivot_reference_contract,
    normalize_kline_open_time,
    primary_target_mode,
    reframe_entry_leg_invalidation,
    reframe_first_displacement_entry,
    reframe_primary_target,
    rejection_displacement,
    repair_kline_flow_frame,
    source_entry_mode,
    source_equilibrium,
    source_equilibrium_detector_enabled,
)


@dataclass(frozen=True, slots=True)
class VoidCloseDecision:
    direction: str
    failed: bool
    boundary: float
    completed_close: float
    signed_distance_from_boundary: float


def void_close_exit_enabled() -> bool:
    return os.environ.get("C10_V46_VOID_CLOSE_EXIT", "0") == "1"


def evaluate_void_close(
    *,
    direction: str,
    completed_close: float,
    zone_low: float,
    zone_high: float,
) -> VoidCloseDecision:
    """Evaluate exact completed-close failure of the entry-owning void."""

    if not zone_low < zone_high:
        raise ValueError("first displacement void must have positive width")
    if direction == "LONG":
        boundary = float(zone_low)
        distance = float(completed_close) - boundary
        failed = distance <= 0.0
    elif direction == "SHORT":
        boundary = float(zone_high)
        distance = boundary - float(completed_close)
        failed = distance <= 0.0
    else:
        raise ValueError(f"unsupported direction: {direction}")
    return VoidCloseDecision(
        direction=direction,
        failed=failed,
        boundary=boundary,
        completed_close=float(completed_close),
        signed_distance_from_boundary=distance,
    )


__all__ = [
    "CostAwareRiskSizer",
    "EntryLegInvalidationDecision",
    "FirstDisplacementEntryDecision",
    "InternalLiquidityCandidate",
    "InternalPivotProtection",
    "LiveImpactLedger",
    "TargetHierarchyDecision",
    "VoidCloseDecision",
    "apply_cost_overlay",
    "build_leadership_gate",
    "certify_plan",
    "consequent_encroachment",
    "evaluate_void_close",
    "first_favorable_internal_pivot",
    "internal_pivot_protection_enabled",
    "invalidation_mode",
    "micro_pivot_protection_enabled",
    "micro_pivot_reference_contract",
    "normalize_kline_open_time",
    "primary_target_mode",
    "reframe_entry_leg_invalidation",
    "reframe_first_displacement_entry",
    "reframe_primary_target",
    "rejection_displacement",
    "repair_kline_flow_frame",
    "source_entry_mode",
    "source_equilibrium",
    "source_equilibrium_detector_enabled",
    "void_close_exit_enabled",
]
