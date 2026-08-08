"""v49 causal cross-market transfer-state router.

v47 established that a source-range failed auction is materially stronger when
the candidate ranks first among BTC, ETH, SOL and XRP in direction-signed
sweep-to-confirmation return.  Rank one alone, however, mixes two distinct
states:

* DISTRIBUTED_TRANSFER: peers have already moved in the proposed direction.
  The candidate must contribute a genuine local confirmation impulse using the
  frozen Candidate 11 follower-confirmation threshold.
* PIONEER_TRANSFER: the peer median has not yet moved in the proposed direction.
  The candidate may lead the transfer only when the proposed FAR direction is a
  true reversal of its own trailing directional drift, not a late same-direction
  continuation mislabeled as a failed auction.

No PnL, future observation, symbol whitelist, fitted return threshold, position
size multiplier or new numerical constant is introduced.  The only magnitude
threshold is the already-existing Candidate 11 minimum follower confirmation
impulse supplied by the live leadership gate; all other decisions use signs and
ordinal rank.
"""
from __future__ import annotations

from dataclasses import dataclass
import os
from typing import Any

from c10_v47_overlay import (  # re-export frozen lower layers
    CostAwareRiskSizer,
    EntryLegInvalidationDecision,
    EventLeaderDecision,
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
    event_leader_only_enabled,
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
    require_event_direction_leader,
    source_entry_mode,
    source_equilibrium,
    source_equilibrium_detector_enabled,
    void_close_exit_enabled,
)


@dataclass(frozen=True, slots=True)
class TransferStateDecision:
    approved: bool
    reason: str
    state: str
    details: dict[str, Any]


def transfer_state_router_enabled() -> bool:
    return os.environ.get("C10_V49_TRANSFER_STATE_ROUTER", "0") == "1"


def _finite_float(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    if result != result or result in (float("inf"), float("-inf")):
        return None
    return result


def classify_transfer_state(
    plan: Any,
    *,
    minimum_confirmation_impulse: float,
) -> TransferStateDecision:
    """Classify and approve one rank-one FAR transfer state.

    The caller supplies the existing Candidate 11 follower-confirmation
    threshold from the instantiated market-leadership gate.  This avoids
    hard-coding or refitting a new magnitude threshold in this layer.
    """

    enabled = transfer_state_router_enabled()
    leadership_raw = getattr(plan, "details", {}).get("market_leadership", {})
    leadership = leadership_raw if isinstance(leadership_raw, dict) else {}
    symbol = str(leadership.get("symbol") or getattr(plan, "symbol", ""))
    rank_raw = leadership.get("event_direction_rank")
    try:
        rank = None if rank_raw is None else int(rank_raw)
    except (TypeError, ValueError):
        rank = None
    peer_median = _finite_float(leadership.get("peer_event_median"))
    impulse = _finite_float(leadership.get("confirmation_impulse"))
    trend_scores_raw = leadership.get("directional_trend_scores", {})
    trend_scores = trend_scores_raw if isinstance(trend_scores_raw, dict) else {}
    candidate_trend = _finite_float(trend_scores.get(symbol))
    threshold = _finite_float(minimum_confirmation_impulse)

    common = {
        "schema": "candidate-10-v49-cross-market-transfer-state-v1",
        "enabled": enabled,
        "symbol": symbol,
        "scenario": leadership.get("scenario"),
        "direction": leadership.get("direction"),
        "event_direction_rank": rank,
        "candidate_event_move": leadership.get("candidate_event_move"),
        "peer_event_median": peer_median,
        "confirmation_impulse": impulse,
        "candidate_trailing_directional_trend_score": candidate_trend,
        "minimum_confirmation_impulse": threshold,
        "threshold_source": (
            "frozen Candidate 11 MarketLeadershipGate."
            "minimum_follower_confirmation_impulse"
        ),
        "state_contract": {
            "DISTRIBUTED_TRANSFER": (
                "peer median is positive in the proposed direction; candidate "
                "confirmation impulse must meet the frozen existing threshold"
            ),
            "PIONEER_TRANSFER": (
                "peer median is non-positive; candidate trailing directional "
                "trend score must be negative so FAR reverses, rather than "
                "extends, its preceding directional auction"
            ),
        },
        "not_used": [
            "future observations",
            "PnL or trade outcome",
            "symbol whitelist",
            "new fitted return threshold",
            "risk multiplier",
            "void-close post-entry outcome",
        ],
        "new_fitted_thresholds": [],
    }

    if not enabled:
        return TransferStateDecision(
            approved=True,
            reason="TRANSFER_STATE_ROUTER_DISABLED",
            state="UNROUTED",
            details={**common, "applied": False},
        )
    if rank != 1:
        return TransferStateDecision(
            approved=False,
            reason="TRANSFER_STATE_REQUIRES_EVENT_DIRECTION_RANK_ONE",
            state="UNRESOLVED",
            details={**common, "applied": True},
        )
    if peer_median is None or impulse is None or candidate_trend is None:
        return TransferStateDecision(
            approved=False,
            reason="TRANSFER_STATE_INPUT_UNAVAILABLE",
            state="UNRESOLVED",
            details={**common, "applied": True},
        )
    if threshold is None or threshold <= 0.0:
        return TransferStateDecision(
            approved=False,
            reason="INVALID_FROZEN_CONFIRMATION_IMPULSE_THRESHOLD",
            state="UNRESOLVED",
            details={**common, "applied": True},
        )

    if peer_median > 0.0:
        state = "DISTRIBUTED_TRANSFER"
        approved = impulse >= threshold
        reason = (
            "DISTRIBUTED_TRANSFER_CONFIRMED"
            if approved
            else "DISTRIBUTED_TRANSFER_WEAK_LOCAL_CONFIRMATION"
        )
    else:
        state = "PIONEER_TRANSFER"
        approved = candidate_trend < 0.0
        reason = (
            "PIONEER_TRANSFER_TRUE_REVERSAL"
            if approved
            else "PIONEER_TRANSFER_NOT_A_TRAILING_REVERSAL"
        )

    return TransferStateDecision(
        approved=approved,
        reason=reason,
        state=state,
        details={**common, "applied": True, "state": state},
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
    "TransferStateDecision",
    "VoidCloseDecision",
    "apply_cost_overlay",
    "build_leadership_gate",
    "certify_plan",
    "classify_transfer_state",
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
    "transfer_state_router_enabled",
    "void_close_exit_enabled",
]
