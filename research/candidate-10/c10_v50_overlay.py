"""v50 independent external-draw event-leader router.

This lineage deliberately leaves the v40 source-equilibrium family.  It keeps
Candidate 11's original failed-auction detector, requires the v29 certificate
that the reversal target is an independently pre-existing external liquidity
hazard, and varies one causal variable only: whether the candidate ranks first
among synchronized BTC/ETH/SOL/XRP markets in direction-signed
sweep-to-confirmation return.
"""
from __future__ import annotations

from dataclasses import dataclass
import os
from typing import Any

from c10_v29_overlay import (
    CostAwareRiskSizer,
    LiveImpactLedger,
    apply_cost_overlay,
    build_leadership_gate,
    certify_plan,
    repair_kline_flow_frame,
)


@dataclass(frozen=True, slots=True)
class ExternalEventLeaderDecision:
    approved: bool
    reason: str
    event_direction_rank: int | None
    details: dict[str, Any]


def external_event_leader_enabled() -> bool:
    return os.environ.get("C10_V50_EXTERNAL_EVENT_LEADER", "0") == "1"


def require_external_event_leader(plan: Any) -> ExternalEventLeaderDecision:
    enabled = external_event_leader_enabled()
    raw = getattr(plan, "details", {}).get("market_leadership", {})
    leadership = raw if isinstance(raw, dict) else {}
    rank_raw = leadership.get("event_direction_rank")
    try:
        rank = None if rank_raw is None else int(rank_raw)
    except (TypeError, ValueError):
        rank = None
    common = {
        "schema": "candidate-10-v50-independent-external-event-leader-v1",
        "enabled": enabled,
        "scenario": leadership.get("scenario"),
        "symbol": leadership.get("symbol"),
        "direction": leadership.get("direction"),
        "event_direction_rank": rank,
        "candidate_event_move": leadership.get("candidate_event_move"),
        "peer_event_median": leadership.get("peer_event_median"),
        "draw_contract": "PREEXISTING_EXTERNAL_HAZARD_DOMINANCE",
        "target_contract": "ORIGINAL_INDEPENDENT_EXTERNAL_LIQUIDITY_DRAW",
        "rank_contract": (
            "1 + synchronized peers with larger direction-signed "
            "sweep-to-confirmation return"
        ),
        "not_used": [
            "future observations",
            "PnL or trade outcome",
            "symbol whitelist",
            "return magnitude threshold",
            "source-equilibrium target",
            "risk multiplier",
        ],
        "new_fitted_thresholds": [],
    }
    if not enabled:
        return ExternalEventLeaderDecision(
            True,
            "EXTERNAL_EVENT_LEADER_ROUTER_DISABLED",
            rank,
            {**common, "applied": False},
        )
    if getattr(getattr(plan, "scenario", None), "value", None) != "FAR":
        return ExternalEventLeaderDecision(
            False,
            "EXTERNAL_EVENT_LEADER_REQUIRES_FAR",
            rank,
            {**common, "applied": True},
        )
    draw_method = str(getattr(plan, "details", {}).get("draw_method", ""))
    if draw_method != "EXTERNAL_HAZARD_DOMINANCE":
        return ExternalEventLeaderDecision(
            False,
            "EXTERNAL_EVENT_LEADER_REQUIRES_INDEPENDENT_DRAW",
            rank,
            {**common, "applied": True, "draw_method": draw_method},
        )
    if rank is None:
        return ExternalEventLeaderDecision(
            False,
            "EXTERNAL_EVENT_DIRECTION_RANK_UNAVAILABLE",
            None,
            {**common, "applied": True},
        )
    if rank != 1:
        return ExternalEventLeaderDecision(
            False,
            "EXTERNAL_CANDIDATE_NOT_EVENT_DIRECTION_LEADER",
            rank,
            {**common, "applied": True},
        )
    return ExternalEventLeaderDecision(
        True,
        "EXTERNAL_CANDIDATE_IS_EVENT_DIRECTION_LEADER",
        rank,
        {**common, "applied": True},
    )


__all__ = [
    "CostAwareRiskSizer",
    "ExternalEventLeaderDecision",
    "LiveImpactLedger",
    "apply_cost_overlay",
    "build_leadership_gate",
    "certify_plan",
    "external_event_leader_enabled",
    "repair_kline_flow_frame",
    "require_external_event_leader",
]
