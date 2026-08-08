"""v47 causal event-direction leadership router.

The v40 source-range failed-auction detector, v41 first-displacement near-edge
entry, source-equilibrium target, original source-raid hard stop, all-cost risk
sizing and global portfolio slot are frozen.  v46 showed that completed void
failure is a useful loss detector but also exits genuine winners.  This layer
therefore acts before order submission and asks a different market-state
question: among BTC, ETH, SOL and XRP, is the candidate itself leading the
proposed directional recovery from the synchronized sweep timestamp through the
confirmation timestamp?

The rank is already computed by the frozen Candidate 11 market-leadership gate
using only completed synchronized observations visible at confirmation.  Rank
one is ordinal, dimensionless and parameter-free; it does not use a return
threshold, symbol whitelist, PnL, future bar, position size or the separate
quote-notional ``leader`` identity.
"""
from __future__ import annotations

from dataclasses import dataclass
import os
from typing import Any

from c10_v46_overlay import (  # re-export frozen lower layers
    CostAwareRiskSizer,
    EntryLegInvalidationDecision,
    FirstDisplacementEntryDecision,
    InternalLiquidityCandidate,
    InternalPivotProtection,
    LiveImpactLedger,
    TargetHierarchyDecision,
    VoidCloseDecision,
    apply_cost_overlay,
    build_leadership_gate,
    certify_plan,
    consequent_encroachment,
    evaluate_void_close,
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
    void_close_exit_enabled,
)


@dataclass(frozen=True, slots=True)
class EventLeaderDecision:
    approved: bool
    reason: str
    event_direction_rank: int | None
    details: dict[str, Any]


def event_leader_only_enabled() -> bool:
    return os.environ.get("C10_V47_EVENT_LEADER_ONLY", "0") == "1"


def require_event_direction_leader(plan: Any) -> EventLeaderDecision:
    """Require the candidate to rank first in its proposed event direction."""

    enabled = event_leader_only_enabled()
    leadership_raw = getattr(plan, "details", {}).get("market_leadership", {})
    leadership = leadership_raw if isinstance(leadership_raw, dict) else {}
    rank_raw = leadership.get("event_direction_rank")
    try:
        rank = None if rank_raw is None else int(rank_raw)
    except (TypeError, ValueError):
        rank = None
    common = {
        "schema": "candidate-10-v47-event-direction-leader-v1",
        "enabled": enabled,
        "candidate_symbol": leadership.get("symbol"),
        "scenario": leadership.get("scenario"),
        "direction": leadership.get("direction"),
        "sweep_ts_ns": leadership.get("sweep_ts_ns"),
        "confirmation_ts_ns": leadership.get("confirmation_ts_ns"),
        "event_direction_rank": rank,
        "candidate_event_move": leadership.get("candidate_event_move"),
        "peer_event_median": leadership.get("peer_event_median"),
        "peer_returns": leadership.get("peer_returns", {}),
        "quote_notional_leader": leadership.get("leader"),
        "rank_contract": (
            "1 + count of synchronized peers with a larger direction-signed "
            "sweep-to-confirmation return"
        ),
        "approval_contract": "EVENT_DIRECTION_RANK_EQUALS_ONE",
        "not_used": [
            "future observations",
            "PnL or trade outcome",
            "fixed return threshold",
            "symbol whitelist",
            "quote-notional leader identity",
            "risk multiplier",
        ],
        "new_fitted_thresholds": [],
    }
    if not enabled:
        return EventLeaderDecision(
            approved=True,
            reason="EVENT_DIRECTION_LEADER_ROUTER_DISABLED",
            event_direction_rank=rank,
            details={**common, "applied": False},
        )
    if rank is None:
        return EventLeaderDecision(
            approved=False,
            reason="EVENT_DIRECTION_RANK_UNAVAILABLE",
            event_direction_rank=None,
            details={**common, "applied": True},
        )
    if rank < 1:
        return EventLeaderDecision(
            approved=False,
            reason="INVALID_EVENT_DIRECTION_RANK",
            event_direction_rank=rank,
            details={**common, "applied": True},
        )
    if rank != 1:
        return EventLeaderDecision(
            approved=False,
            reason="CANDIDATE_NOT_EVENT_DIRECTION_LEADER",
            event_direction_rank=rank,
            details={**common, "applied": True},
        )
    return EventLeaderDecision(
        approved=True,
        reason="CANDIDATE_IS_EVENT_DIRECTION_LEADER",
        event_direction_rank=rank,
        details={**common, "applied": True},
    )


__all__ = [
    "CostAwareRiskSizer",
    "EntryLegInvalidationDecision",
    "EventLeaderDecision",
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
    "event_leader_only_enabled",
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
    "require_event_direction_leader",
    "source_entry_mode",
    "source_equilibrium",
    "source_equilibrium_detector_enabled",
    "void_close_exit_enabled",
]
