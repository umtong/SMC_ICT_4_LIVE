"""v31 efficient-liquidity-raid certificate over v30/v29/v28/v27.

A failed-auction reversal begins with an external liquidity raid.  The raid must
itself be an efficient price-discovery excursion, not merely large turnover
stalled at the boundary.  Its penetration in ATR units per unit of relative
volume must meet the already-frozen displacement-body threshold.  No new fitted
number is introduced: the same semantic minimum defines meaningful price
progress in the confirmation leg.
"""
from __future__ import annotations

from dataclasses import replace
import os
from typing import Any

from c10_v30_overlay import (  # re-export frozen lower layers
    CostAwareRiskSizer,
    LiveImpactLedger,
    apply_cost_overlay,
    build_leadership_gate,
    certify_plan,
    cost_neutral_stop,
    equilibrium_enabled,
    equilibrium_reached,
    far_only_enabled,
    repair_kline_flow_frame,
    source_midpoint,
)


def certify_sweep_efficiency(plan: Any, decision: Any, logic: Any) -> Any:
    if (
        os.environ.get("C10_V31_ABLATE_SWEEP_EFFICIENCY", "0") == "1"
        or not decision.approved
        or getattr(getattr(plan, "scenario", None), "value", None) != "FAR"
    ):
        return decision

    sweep = next(
        (
            event
            for event in reversed(logic.events)
            if event.scenario_id == plan.scenario_id
            and event.event_type == "LIQUIDITY_SWEEP"
        ),
        None,
    )
    if sweep is None:
        return replace(
            decision,
            approved=False,
            reason="SWEEP_EFFICIENCY_EVIDENCE_MISSING",
        )
    details = dict(sweep.details)
    penetration = float(details.get("penetration_atr", 0.0) or 0.0)
    relative_volume = float(details.get("relative_volume", 0.0) or 0.0)
    efficiency = penetration / max(relative_volume, 1e-12)
    threshold = float(logic.config.displacement_body_atr)
    plan.details["sweep_excursion_efficiency"] = efficiency
    plan.details["sweep_excursion_efficiency_threshold"] = threshold
    plan.details["sweep_penetration_atr"] = penetration
    plan.details["sweep_relative_volume"] = relative_volume
    if efficiency < threshold:
        return replace(
            decision,
            approved=False,
            reason="INEFFICIENT_LIQUIDITY_RAID_FOR_FAR",
        )
    return replace(
        decision,
        reason=f"EFFICIENT_RAID_{decision.reason}",
    )
