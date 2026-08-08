"""v45 entry-leg invalidation for the v44 causal target hierarchy.

The v40 source failed-auction detector and v41 first-displacement near-edge
passive entry are frozen.  v44 showed that every still-live preconfirmed
five-minute internal objective failed the existing costed-R floor when risk was
measured to the earlier source-raid extreme.  This layer tests whether that is a
target failure or an auction-leg ownership error.

The only new invalidation is the opposite edge of the already-confirmed first
displacement execution void plus the frozen ATR buffer.  LONG risk ends below
the void low; SHORT risk ends above the void high.  The stop must be strictly
inside the original source-raid invalidation, remain beyond the entry, satisfy
the existing minimum stop-distance floor and clear the existing costed-R floor.
No new distance, wick, close, MFE, percentile or fitted threshold is introduced.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
import os
from typing import Any

from c10_v44_overlay import (  # re-export frozen lower layers
    CostAwareRiskSizer,
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
    micro_pivot_protection_enabled,
    micro_pivot_reference_contract,
    normalize_kline_open_time,
    primary_target_mode,
    reframe_first_displacement_entry,
    reframe_primary_target,
    rejection_displacement,
    repair_kline_flow_frame,
    source_entry_mode,
    source_equilibrium,
    source_equilibrium_detector_enabled,
)


@dataclass(frozen=True, slots=True)
class EntryLegInvalidationDecision:
    approved: bool
    plan: Any
    reason: str
    details: dict[str, Any]


def invalidation_mode() -> str:
    value = os.environ.get(
        "C10_V45_INVALIDATION_MODE",
        "SOURCE_RAID_EXTREME",
    )
    if value not in {
        "SOURCE_RAID_EXTREME",
        "FIRST_DISPLACEMENT_VOID_FAR_EDGE",
    }:
        raise ValueError(f"unsupported v45 invalidation mode: {value}")
    return value


def _value(value: Any) -> str:
    return str(getattr(value, "value", value))


def reframe_entry_leg_invalidation(
    plan: Any,
    logic: Any,
) -> EntryLegInvalidationDecision:
    """Own risk at the far edge of the displacement leg that owns entry."""

    mode = invalidation_mode()
    common = {
        "schema": "candidate-10-v45-entry-leg-invalidation-v1",
        "invalidation_mode": mode,
        "detector": "SOURCE_SWEEP_RECLAIM_MSS_DISPLACEMENT",
        "entry": "FIRST_DISPLACEMENT_NEAR_EDGE_PASSIVE_RETRACE",
        "buffer": "FROZEN_STOP_BUFFER_ATR",
        "new_fitted_thresholds": [],
    }
    if mode == "SOURCE_RAID_EXTREME":
        return EntryLegInvalidationDecision(
            approved=True,
            plan=plan,
            reason="SOURCE_RAID_INVALIDATION_UNCHANGED",
            details={**common, "applied": False},
        )
    if _value(getattr(plan, "scenario", "")) != "FAR":
        return EntryLegInvalidationDecision(
            approved=True,
            plan=plan,
            reason="NON_FAR_UNCHANGED",
            details={**common, "applied": False},
        )

    zone_low_raw = plan.details.get("zone_low")
    zone_high_raw = plan.details.get("zone_high")
    if zone_low_raw is None or zone_high_raw is None:
        return EntryLegInvalidationDecision(
            approved=False,
            plan=plan,
            reason="FIRST_DISPLACEMENT_VOID_UNAVAILABLE",
            details={**common, "applied": False},
        )
    zone_low = float(zone_low_raw)
    zone_high = float(zone_high_raw)
    if not zone_low < zone_high:
        return EntryLegInvalidationDecision(
            approved=False,
            plan=plan,
            reason="INVALID_FIRST_DISPLACEMENT_VOID",
            details={
                **common,
                "applied": False,
                "zone_low": zone_low,
                "zone_high": zone_high,
            },
        )

    direction = _value(plan.direction)
    entry = float(plan.expected_entry)
    original_stop = float(plan.stop_price)
    target = float(plan.target_price)
    atr = float(plan.atr)
    buffer = float(logic.config.stop_buffer_atr) * atr
    if direction == "LONG":
        structural_boundary = zone_low
        stop = structural_boundary - buffer
        causal_order = original_stop < stop < entry
        risk = entry - stop
        reward = target - entry
    elif direction == "SHORT":
        structural_boundary = zone_high
        stop = structural_boundary + buffer
        causal_order = entry < stop < original_stop
        risk = stop - entry
        reward = entry - target
    else:
        raise ValueError(f"unsupported direction: {direction}")

    details = {
        **common,
        "applied": True,
        "direction": direction,
        "entry": entry,
        "zone_low": zone_low,
        "zone_high": zone_high,
        "structural_boundary": structural_boundary,
        "atr": atr,
        "buffer_distance": buffer,
        "original_source_raid_stop": original_stop,
        "selected_entry_leg_stop": stop,
        "target_before_target_hierarchy": target,
        "gross_risk": risk,
        "gross_reward": reward,
        "state_sequence": [
            "SOURCE_EQUILIBRIUM_FAILED_AUCTION_CONFIRMED",
            "FIRST_DISPLACEMENT_VOID_DEFINED",
            "FIRST_DISPLACEMENT_NEAR_EDGE_RETRACE_PENDING",
            "VOID_FAR_EDGE_INVALIDATION_OR_TARGET_DELIVERY",
        ],
    }
    if not causal_order or risk <= 0.0 or reward <= 0.0:
        return EntryLegInvalidationDecision(
            approved=False,
            plan=plan,
            reason="ENTRY_LEG_INVALIDATION_NON_CAUSAL_PRICE_ORDER",
            details=details,
        )
    if atr <= 0.0 or risk / atr < float(logic.config.min_stop_atr):
        details["risk_atr"] = None if atr <= 0.0 else risk / atr
        details["minimum_existing_stop_atr"] = float(
            logic.config.min_stop_atr,
        )
        return EntryLegInvalidationDecision(
            approved=False,
            plan=plan,
            reason="ENTRY_LEG_STOP_DISTANCE_BELOW_EXECUTION_FLOOR",
            details=details,
        )

    maker = float(logic.config.effective_maker_rate)
    taker = float(logic.config.effective_taker_rate)
    loss = risk + entry * maker + stop * taker
    gain = reward - entry * maker - target * maker
    net_r = gain / loss if loss > 0.0 else float("-inf")
    details.update(
        {
            "risk_atr": risk / atr,
            "loss_per_unit_before_impact": loss,
            "gain_per_unit_before_impact": gain,
            "costed_structural_r_before_impact": net_r,
            "minimum_existing_costed_structural_r": float(
                logic.config.min_net_r,
            ),
        },
    )
    if gain <= 0.0 or net_r < float(logic.config.min_net_r):
        return EntryLegInvalidationDecision(
            approved=False,
            plan=plan,
            reason="ENTRY_LEG_INVALIDATION_INSUFFICIENT_COSTED_STRUCTURAL_R",
            details=details,
        )

    plan_details = dict(plan.details)
    ce_primary = dict(plan_details.get("ce_rejection_primary", {}))
    ce_primary["final_retest_invalidation"] = stop
    ce_primary["initial_raid_invalidation"] = original_stop
    plan_details["ce_rejection_primary"] = ce_primary
    plan_details["source_entry_leg_invalidation"] = details
    reframed = replace(
        plan,
        stop_price=stop,
        loss_per_unit=loss,
        gain_per_unit=gain,
        net_r=net_r,
        reason_code="SOURCE_FAILED_AUCTION_FIRST_VOID_FAR_EDGE_INVALIDATION",
        details=plan_details,
    )
    return EntryLegInvalidationDecision(
        approved=True,
        plan=reframed,
        reason="SOURCE_FAILED_AUCTION_FIRST_VOID_FAR_EDGE_INVALIDATION",
        details=details,
    )


__all__ = [
    "CostAwareRiskSizer",
    "EntryLegInvalidationDecision",
    "FirstDisplacementEntryDecision",
    "InternalLiquidityCandidate",
    "InternalPivotProtection",
    "LiveImpactLedger",
    "TargetHierarchyDecision",
    "apply_cost_overlay",
    "build_leadership_gate",
    "certify_plan",
    "consequent_encroachment",
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
]
