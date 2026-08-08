"""Execution exports and event-leader router for v63 flow continuation."""
from __future__ import annotations

from dataclasses import dataclass
import os
from typing import Any

from c10_v27_overlay import (
    CostAwareRiskSizer,
    LiveImpactLedger,
    apply_cost_overlay,
    build_leadership_gate,
)


@dataclass(frozen=True, slots=True)
class FlowEventLeaderDecision:
    approved: bool
    reason: str
    event_direction_rank: int | None
    details: dict[str, Any]


def flow_event_leader_enabled() -> bool:
    return os.environ.get("C10_V63_EVENT_LEADER_ONLY", "1") == "1"


def require_flow_event_leader(plan: Any) -> FlowEventLeaderDecision:
    """Require the flow-continuation candidate to lead the event direction."""

    enabled = flow_event_leader_enabled()
    leadership_raw = getattr(plan, "details", {}).get("market_leadership", {})
    leadership = leadership_raw if isinstance(leadership_raw, dict) else {}
    raw = leadership.get("event_direction_rank")
    try:
        rank = None if raw is None else int(raw)
    except (TypeError, ValueError):
        rank = None
    common = {
        "schema": "candidate-10-v63-flow-event-leader-v1",
        "enabled": enabled,
        "symbol": leadership.get("symbol"),
        "direction": leadership.get("direction"),
        "event_direction_rank": rank,
        "candidate_event_move": leadership.get("candidate_event_move"),
        "peer_event_median": leadership.get("peer_event_median"),
        "peer_returns": leadership.get("peer_returns", {}),
        "event_path_efficiency": leadership.get("event_path_efficiency"),
        "event_standardized_displacement": leadership.get(
            "event_standardized_displacement"
        ),
        "confirmation_impulse": leadership.get("confirmation_impulse"),
        "approval_contract": "EVENT_DIRECTION_RANK_EQUALS_ONE",
        "new_fitted_thresholds": [],
    }
    if not enabled:
        return FlowEventLeaderDecision(
            True,
            "FLOW_EVENT_LEADER_ROUTER_DISABLED",
            rank,
            {**common, "applied": False},
        )
    if rank is None:
        return FlowEventLeaderDecision(
            False,
            "FLOW_EVENT_DIRECTION_RANK_UNAVAILABLE",
            None,
            {**common, "applied": True},
        )
    if rank != 1:
        return FlowEventLeaderDecision(
            False,
            "FLOW_CANDIDATE_NOT_EVENT_LEADER",
            rank,
            {**common, "applied": True},
        )
    return FlowEventLeaderDecision(
        True,
        "FLOW_CANDIDATE_IS_EVENT_LEADER",
        rank,
        {**common, "applied": True},
    )


__all__ = [
    "CostAwareRiskSizer",
    "FlowEventLeaderDecision",
    "LiveImpactLedger",
    "apply_cost_overlay",
    "build_leadership_gate",
    "flow_event_leader_enabled",
    "require_flow_event_leader",
]
