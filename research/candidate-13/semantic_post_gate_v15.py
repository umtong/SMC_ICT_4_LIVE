"""Candidate 13 V15 post-leadership execution boundary.

Leadership may approve or reject the scenario and may label its semantic role,
but it cannot mutate a causal FAR retrace LIMIT into a confirmation-close
MARKET order or replace the sweep-based structural stop with an internal void
stop.
"""
from __future__ import annotations

from dataclasses import replace

from logic import CausalAuctionEngine, TradePlan
from market_leadership import LeadershipDecision


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
    """Record the synchronized role while preserving the causal execution plan."""
    details = dict(plan.details)
    details.update(
        {
            "post_leadership_role": decision.reason,
            "post_leadership_approved": decision.approved,
            "post_leadership_execution_policy": "PRESERVE_CAUSAL_RETRACE_AND_STRUCTURAL_STOP",
            "post_leadership_execution_reclassified": False,
        },
    )
    amended = replace(plan, details=details)
    _amend_event(
        engine,
        plan.scenario_id,
        {
            "post_leadership_role": decision.reason,
            "post_leadership_approved": decision.approved,
            "post_leadership_execution_policy": "PRESERVE_CAUSAL_RETRACE_AND_STRUCTURAL_STOP",
            "post_leadership_execution_reclassified": False,
            "entry_order_type": amended.entry_order_type,
            "entry_post_only": amended.entry_post_only,
            "expected_entry": amended.expected_entry,
            "stop": amended.stop_price,
            "net_r": amended.net_r,
            "expire_ts_ns": amended.expire_ts_ns,
        },
    )
    return amended
