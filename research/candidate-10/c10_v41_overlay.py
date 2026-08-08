"""v41 source-equilibrium entry-timing attribution.

The v40 decoupled source-range detector and source-equilibrium primary target are
frozen.  This layer changes only when the already-confirmed failed auction may
own a passive entry:

* first displacement near edge (the frozen Candidate 11 execution-void edge),
* first displacement consequent encroachment (the exact void midpoint), or
* the v36 CE touch and second rejection-displacement retrace.

No fitted retracement, score, MFE, or additional signal condition is added.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
import os
from typing import Any

from c10_v40_overlay import (  # re-export frozen lower layers
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
    source_equilibrium_detector_enabled,
)


@dataclass(frozen=True, slots=True)
class FirstDisplacementEntryDecision:
    approved: bool
    plan: Any
    reason: str
    details: dict[str, Any]


def source_entry_mode() -> str:
    value = os.environ.get(
        "C10_V41_SOURCE_ENTRY_MODE",
        "SECOND_REJECTION_DISPLACEMENT",
    )
    if value not in {
        "FIRST_DISPLACEMENT_NEAR_EDGE",
        "FIRST_DISPLACEMENT_CE",
        "SECOND_REJECTION_DISPLACEMENT",
    }:
        raise ValueError(f"unsupported v41 source entry mode: {value}")
    return value


def reframe_first_displacement_entry(
    plan: Any,
    logic: Any,
) -> FirstDisplacementEntryDecision:
    """Move only the first-displacement passive entry from near edge to CE."""

    mode = source_entry_mode()
    common = {
        "schema": "candidate-10-v41-source-entry-timing-v1",
        "entry_mode": mode,
        "detector": "SOURCE_SWEEP_RECLAIM_MSS_DISPLACEMENT",
        "primary_target": "SOURCE_DEALING_RANGE_EQUILIBRIUM",
        "initial_invalidation": "SOURCE_RAID_EXTREME_PLUS_FROZEN_ATR_BUFFER",
    }
    if mode != "FIRST_DISPLACEMENT_CE":
        return FirstDisplacementEntryDecision(
            approved=True,
            plan=plan,
            reason="ENTRY_MODE_UNCHANGED",
            details={**common, "applied": False},
        )
    if str(getattr(getattr(plan, "scenario", None), "value", plan.scenario)) != "FAR":
        return FirstDisplacementEntryDecision(
            approved=True,
            plan=plan,
            reason="NON_FAR_UNCHANGED",
            details={**common, "applied": False},
        )

    zone_low_raw = plan.details.get("zone_low")
    zone_high_raw = plan.details.get("zone_high")
    confirmation_raw = plan.details.get("confirmation_close")
    if zone_low_raw is None or zone_high_raw is None or confirmation_raw is None:
        return FirstDisplacementEntryDecision(
            approved=False,
            plan=plan,
            reason="FIRST_DISPLACEMENT_ZONE_UNAVAILABLE",
            details={**common, "applied": False},
        )
    zone_low = float(zone_low_raw)
    zone_high = float(zone_high_raw)
    confirmation = float(confirmation_raw)
    if not zone_low < zone_high:
        return FirstDisplacementEntryDecision(
            approved=False,
            plan=plan,
            reason="INVALID_FIRST_DISPLACEMENT_ZONE",
            details={
                **common,
                "applied": False,
                "zone_low": zone_low,
                "zone_high": zone_high,
            },
        )

    entry = consequent_encroachment(zone_low, zone_high)
    direction = str(getattr(plan.direction, "value", plan.direction))
    stop = float(plan.stop_price)
    target = float(plan.target_price)
    if direction == "LONG":
        risk = entry - stop
        reward = target - entry
        passive = entry < confirmation
    elif direction == "SHORT":
        risk = stop - entry
        reward = entry - target
        passive = entry > confirmation
    else:
        raise ValueError(f"unsupported direction: {direction}")

    details = {
        **common,
        "applied": True,
        "direction": direction,
        "zone_low": zone_low,
        "zone_high": zone_high,
        "first_displacement_near_edge": float(plan.expected_entry),
        "first_displacement_ce": entry,
        "confirmation_close": confirmation,
        "stop": stop,
        "target": target,
        "gross_risk": risk,
        "gross_reward": reward,
        "state_sequence": [
            "SOURCE_EQUILIBRIUM_FAILED_AUCTION_CONFIRMED",
            "FIRST_DISPLACEMENT_CE_RETRACE_PENDING",
            "POSITION_OR_ENTRY_EXPIRED",
        ],
    }
    if not passive or risk <= 0.0 or reward <= 0.0:
        return FirstDisplacementEntryDecision(
            approved=False,
            plan=plan,
            reason="FIRST_DISPLACEMENT_CE_NON_CAUSAL_PRICE_ORDER",
            details=details,
        )

    maker = float(logic.config.effective_maker_rate)
    taker = float(logic.config.effective_taker_rate)
    loss = risk + entry * maker + stop * taker
    gain = reward - entry * maker - target * maker
    net_r = gain / loss if loss > 0.0 else float("-inf")
    details.update(
        {
            "loss_per_unit_before_impact": loss,
            "gain_per_unit_before_impact": gain,
            "costed_structural_r_before_impact": net_r,
            "minimum_existing_costed_structural_r": float(logic.config.min_net_r),
        },
    )
    if gain <= 0.0 or net_r < float(logic.config.min_net_r):
        return FirstDisplacementEntryDecision(
            approved=False,
            plan=plan,
            reason="FIRST_DISPLACEMENT_CE_INSUFFICIENT_COSTED_STRUCTURAL_R",
            details=details,
        )

    plan_details = dict(plan.details)
    ce_primary = dict(plan_details.get("ce_rejection_primary", {}))
    ce_primary.update(
        {
            "entry_process": "FIRST_DISPLACEMENT_CE_RETRACE",
            "consequent_encroachment": entry,
            "final_retest_invalidation": stop,
            "selected_target": target,
            "state_sequence": details["state_sequence"],
        },
    )
    plan_details["ce_rejection_primary"] = ce_primary
    plan_details["source_entry_timing"] = details
    reframed = replace(
        plan,
        expected_entry=entry,
        loss_per_unit=loss,
        gain_per_unit=gain,
        net_r=net_r,
        reason_code="SOURCE_EQUILIBRIUM_FIRST_DISPLACEMENT_CE_RETRACE",
        details=plan_details,
    )
    return FirstDisplacementEntryDecision(
        approved=True,
        plan=reframed,
        reason="SOURCE_EQUILIBRIUM_FIRST_DISPLACEMENT_CE_RETRACE",
        details=details,
    )


__all__ = [
    "CostAwareRiskSizer",
    "FirstDisplacementEntryDecision",
    "InternalPivotProtection",
    "LiveImpactLedger",
    "apply_cost_overlay",
    "build_leadership_gate",
    "certify_plan",
    "consequent_encroachment",
    "first_favorable_internal_pivot",
    "internal_pivot_protection_enabled",
    "micro_pivot_protection_enabled",
    "micro_pivot_reference_contract",
    "normalize_kline_open_time",
    "reframe_first_displacement_entry",
    "rejection_displacement",
    "repair_kline_flow_frame",
    "source_entry_mode",
    "source_equilibrium",
    "source_equilibrium_detector_enabled",
]
