"""All-cost exports and causal acceptance-state router for v64."""
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
class IntradayAcceptanceDecision:
    approved: bool
    reason: str
    state: str
    event_direction_rank: int | None
    details: dict[str, Any]


def resolved_acceptance_only_enabled() -> bool:
    return os.environ.get("C10_V64_RESOLVED_ACCEPTANCE_ONLY", "1") == "1"


def _optional_float(value: Any) -> float | None:
    try:
        return None if value is None else float(value)
    except (TypeError, ValueError):
        return None


def _optional_int(value: Any) -> int | None:
    try:
        return None if value is None else int(value)
    except (TypeError, ValueError):
        return None


def resolve_intraday_acceptance(plan: Any) -> IntradayAcceptanceDecision:
    """Route either distributed or pioneer accepted-price-discovery states.

    DISTRIBUTED_ACCEPTANCE means the candidate and the median synchronized peer
    both delivered in the proposed direction from the causal pivot-known time.
    PIONEER_ACCEPTANCE means the candidate led that event while its last
    completed four-hour auction independently accepted the same direction.
    Everything else remains unresolved rather than being forced into a trade.
    """

    enabled = resolved_acceptance_only_enabled()
    details_raw = getattr(plan, "details", {})
    details = details_raw if isinstance(details_raw, dict) else {}
    leadership_raw = details.get("market_leadership", {})
    leadership = leadership_raw if isinstance(leadership_raw, dict) else {}
    context_raw = details.get("completed_4h_context", {})
    context = context_raw if isinstance(context_raw, dict) else {}

    event_rank = _optional_int(leadership.get("event_direction_rank"))
    candidate_move = _optional_float(leadership.get("candidate_event_move"))
    peer_median = _optional_float(leadership.get("peer_event_median"))
    event_efficiency = _optional_float(leadership.get("event_path_efficiency"))
    event_displacement = _optional_float(
        leadership.get("event_standardized_displacement"),
    )
    local_context_aligned = bool(context.get("aligned", False))
    candidate_delivered = candidate_move is not None and candidate_move > 0.0
    distributed = (
        candidate_delivered
        and peer_median is not None
        and peer_median > 0.0
    )
    pioneer = (
        candidate_delivered
        and event_rank == 1
        and local_context_aligned
    )

    if distributed and pioneer:
        state = "DISTRIBUTED_PIONEER_ACCEPTANCE"
    elif distributed:
        state = "DISTRIBUTED_ACCEPTANCE"
    elif pioneer:
        state = "PIONEER_ACCEPTANCE"
    else:
        state = "UNRESOLVED_ACCEPTANCE"

    common = {
        "schema": "candidate-10-v64-intraday-acceptance-router-v1",
        "enabled": enabled,
        "state": state,
        "candidate_symbol": leadership.get("symbol"),
        "direction": leadership.get("direction"),
        "impulse_start_ts_ns": details.get("impulse_start_ts_ns"),
        "confirmation_ts_ns": leadership.get("confirmation_ts_ns"),
        "candidate_event_move": candidate_move,
        "peer_event_median": peer_median,
        "event_direction_rank": event_rank,
        "event_path_efficiency": event_efficiency,
        "event_standardized_displacement": event_displacement,
        "completed_4h_context_state": context.get("state"),
        "completed_4h_context_aligned": local_context_aligned,
        "distributed_acceptance": distributed,
        "pioneer_acceptance": pioneer,
        "approval_contract": (
            "CANDIDATE_AND_PEER_MEDIAN_SAME_DIRECTION_OR_"
            "EVENT_LEADER_WITH_INDEPENDENT_COMPLETED_4H_ACCEPTANCE"
        ),
        "not_used": [
            "future observations",
            "trade outcome or PnL",
            "symbol whitelist",
            "fixed return magnitude threshold",
            "risk multiplier",
        ],
        "new_fitted_thresholds": [],
    }
    if not enabled:
        return IntradayAcceptanceDecision(
            approved=True,
            reason="INTRADAY_ACCEPTANCE_ROUTER_DISABLED",
            state=state,
            event_direction_rank=event_rank,
            details={**common, "applied": False},
        )
    if candidate_move is None or event_rank is None:
        return IntradayAcceptanceDecision(
            approved=False,
            reason="INTRADAY_ACCEPTANCE_STATE_UNAVAILABLE",
            state="UNRESOLVED_ACCEPTANCE",
            event_direction_rank=event_rank,
            details={**common, "applied": True},
        )
    if state == "UNRESOLVED_ACCEPTANCE":
        return IntradayAcceptanceDecision(
            approved=False,
            reason="INTRADAY_ACCEPTANCE_UNRESOLVED",
            state=state,
            event_direction_rank=event_rank,
            details={**common, "applied": True},
        )
    return IntradayAcceptanceDecision(
        approved=True,
        reason=state,
        state=state,
        event_direction_rank=event_rank,
        details={**common, "applied": True},
    )


__all__ = [
    "CostAwareRiskSizer",
    "IntradayAcceptanceDecision",
    "LiveImpactLedger",
    "apply_cost_overlay",
    "build_leadership_gate",
    "resolve_intraday_acceptance",
    "resolved_acceptance_only_enabled",
]
