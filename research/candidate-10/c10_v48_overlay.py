"""v48 independent accepted-auction continuation event-leader router.

This generation does not modify the v40/v41 failed-auction family.  It returns
to the frozen Candidate 11 AAC scenario as a separate opportunity family:
external-liquidity trade-through, sustained outside acceptance, a causally
right-confirmed pullback, reacceleration with directional flow/body/location,
and a pre-existing independent external continuation target.

The only ablated variable is whether the candidate must also rank first among
BTC, ETH, SOL and XRP in direction-signed sweep-to-confirmation return.  The
rank is frozen Candidate 11 data visible at confirmation.  It is ordinal and
parameter-free; no return threshold, symbol whitelist, PnL input or risk
multiplier is introduced.
"""
from __future__ import annotations

from dataclasses import dataclass
import os
from typing import Any

from c10_v29_overlay import (  # frozen costs, leadership and draw certificate
    CostAwareRiskSizer,
    LiveImpactLedger,
    apply_cost_overlay,
    build_leadership_gate,
    certify_plan,
    repair_kline_flow_frame,
)


@dataclass(frozen=True, slots=True)
class AACEventLeaderDecision:
    approved: bool
    reason: str
    event_direction_rank: int | None
    details: dict[str, Any]


def aac_event_leader_only_enabled() -> bool:
    return os.environ.get("C10_V48_AAC_EVENT_LEADER_ONLY", "0") == "1"


def require_aac_event_direction_leader(plan: Any) -> AACEventLeaderDecision:
    enabled = aac_event_leader_only_enabled()
    details_raw = getattr(plan, "details", {})
    leadership_raw = (
        details_raw.get("market_leadership", {})
        if isinstance(details_raw, dict)
        else {}
    )
    leadership = leadership_raw if isinstance(leadership_raw, dict) else {}
    rank_raw = leadership.get("event_direction_rank")
    try:
        rank = None if rank_raw is None else int(rank_raw)
    except (TypeError, ValueError):
        rank = None
    common = {
        "schema": "candidate-10-v48-aac-event-direction-leader-v1",
        "enabled": enabled,
        "scenario": leadership.get("scenario"),
        "candidate_symbol": leadership.get("symbol"),
        "direction": leadership.get("direction"),
        "sweep_ts_ns": leadership.get("sweep_ts_ns"),
        "confirmation_ts_ns": leadership.get("confirmation_ts_ns"),
        "event_direction_rank": rank,
        "candidate_event_move": leadership.get("candidate_event_move"),
        "peer_event_median": leadership.get("peer_event_median"),
        "peer_returns": leadership.get("peer_returns", {}),
        "quote_notional_leader": leadership.get("leader"),
        "frozen_aac_leadership_reason": leadership.get("reason"),
        "rank_contract": (
            "1 + count of synchronized peers with a larger direction-signed "
            "sweep-to-confirmation return"
        ),
        "approval_contract": "AAC_EVENT_DIRECTION_RANK_EQUALS_ONE",
        "new_fitted_thresholds": [],
        "not_used": [
            "future observations",
            "trade PnL",
            "fixed return threshold",
            "symbol whitelist",
            "risk multiplier",
        ],
    }
    if not enabled:
        return AACEventLeaderDecision(
            approved=True,
            reason="AAC_EVENT_LEADER_ROUTER_DISABLED",
            event_direction_rank=rank,
            details={**common, "applied": False},
        )
    scenario = str(leadership.get("scenario", ""))
    if scenario != "AAC":
        return AACEventLeaderDecision(
            approved=False,
            reason="AAC_EVENT_ROUTER_RECEIVED_NON_AAC_PLAN",
            event_direction_rank=rank,
            details={**common, "applied": True},
        )
    if rank is None:
        return AACEventLeaderDecision(
            approved=False,
            reason="AAC_EVENT_DIRECTION_RANK_UNAVAILABLE",
            event_direction_rank=None,
            details={**common, "applied": True},
        )
    if rank < 1:
        return AACEventLeaderDecision(
            approved=False,
            reason="INVALID_AAC_EVENT_DIRECTION_RANK",
            event_direction_rank=rank,
            details={**common, "applied": True},
        )
    if rank != 1:
        return AACEventLeaderDecision(
            approved=False,
            reason="AAC_CANDIDATE_NOT_EVENT_DIRECTION_LEADER",
            event_direction_rank=rank,
            details={**common, "applied": True},
        )
    return AACEventLeaderDecision(
        approved=True,
        reason="AAC_CANDIDATE_IS_EVENT_DIRECTION_LEADER",
        event_direction_rank=rank,
        details={**common, "applied": True},
    )


__all__ = [
    "AACEventLeaderDecision",
    "CostAwareRiskSizer",
    "LiveImpactLedger",
    "aac_event_leader_only_enabled",
    "apply_cost_overlay",
    "build_leadership_gate",
    "certify_plan",
    "repair_kline_flow_frame",
    "require_aac_event_direction_leader",
]
