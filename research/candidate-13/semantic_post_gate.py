"""Post-leadership execution reclassification for Candidate 13.

The pattern engine cannot know the cross-market semantic role when it first
creates a TradePlan.  After the synchronized leadership decision is available,
a unanimously confirmed common-auction exhaustion may activate the pre-priced
first-void-repair market plan.  Other roles retain their original structural
market or passive execution.
"""
from __future__ import annotations

from dataclasses import replace

from logic import CausalAuctionEngine, Scenario, TradePlan
from market_leadership import LeadershipDecision
from semantic_execution import MARKET_ENTRY_SENTINEL_NS
from semantic_market_leadership import FAR_EXHAUSTION_UNANIMOUS


def _amend_event(
    engine: CausalAuctionEngine,
    scenario_id: str,
    updates: dict[str, object],
) -> None:
    for event in reversed(engine.events):
        if getattr(event, "scenario_id", None) != scenario_id:
            continue
        if getattr(event, "event_type", None) != "TRADE_PLAN_CONFIRMED":
            continue
        details = getattr(event, "details", None)
        if isinstance(details, dict):
            details.update(updates)
        break


def amend_after_leadership(
    engine: CausalAuctionEngine,
    plan: TradePlan,
    decision: LeadershipDecision,
) -> TradePlan:
    """Activate a pre-priced void-repair MARKET plan when its role permits."""
    if (
        not decision.approved
        or plan.scenario != Scenario.FAR
        or plan.entry_order_type == "MARKET"
        or decision.reason != FAR_EXHAUSTION_UNANIMOUS
    ):
        return plan

    raw = plan.details.get("void_repair_candidate")
    if not isinstance(raw, dict) or raw.get("eligible") is not True:
        return plan

    required = (
        "entry",
        "stop",
        "target",
        "loss_per_unit",
        "gain_per_unit",
        "net_r",
    )
    if any(name not in raw for name in required):
        return plan

    details = dict(plan.details)
    details.update(
        {
            "original_passive_entry": plan.expected_entry,
            "original_structural_stop": plan.stop_price,
            "entry_model": "CONFIRMED_EXHAUSTION_VOID_REPAIR_MARKET",
            "stop_model": "FULL_DISPLACEMENT_VOID_REPAIR",
            "entry_cost_assumption": "TAKER",
            "entry_expiry_structure_minutes": 0,
            "market_parent_sentinel_ns": MARKET_ENTRY_SENTINEL_NS,
            "post_leadership_execution_reclassified": True,
            "post_leadership_role": decision.reason,
        },
    )
    amended = replace(
        plan,
        expected_entry=float(raw["entry"]),
        stop_price=float(raw["stop"]),
        target_price=float(raw["target"]),
        loss_per_unit=float(raw["loss_per_unit"]),
        gain_per_unit=float(raw["gain_per_unit"]),
        net_r=float(raw["net_r"]),
        reason_code="FAR_EXHAUSTION_VOID_REPAIR_MARKET",
        expire_ts_ns=MARKET_ENTRY_SENTINEL_NS,
        entry_order_type="MARKET",
        entry_post_only=False,
        details=details,
    )
    _amend_event(
        engine,
        plan.scenario_id,
        {
            "post_leadership_execution_reclassified": True,
            "post_leadership_role": decision.reason,
            "entry_order_type": "MARKET",
            "entry_post_only": False,
            "expected_entry": amended.expected_entry,
            "original_passive_entry": plan.expected_entry,
            "original_structural_stop": plan.stop_price,
            "stop": amended.stop_price,
            "net_r": amended.net_r,
            "entry_cost_assumption": "TAKER",
            "stop_model": "FULL_DISPLACEMENT_VOID_REPAIR",
            "expire_ts_ns": MARKET_ENTRY_SENTINEL_NS,
        },
    )
    return amended
