"""v61 SMT-style peer-nonconfirmation router over v47 event leadership.

Cross-market price-discovery research distinguishes broad information shocks,
which propagate across venues/assets, from idiosyncratic dislocations which are
more likely to reverse.  Discretionary SMT divergence expresses the same idea:
one correlated market raids liquidity while its peers fail to confirm.

The frozen Candidate 11 leadership gate already reports the direction-signed
median peer move from source sweep to confirmation.  This layer uses only its
sign:

* median peer move <= 0: peers have not confirmed the proposed reversal
  direction; the candidate's rank-one local recovery is an idiosyncratic
  nonconfirmation state and may own the FAR trade;
* median peer move > 0: the move is distributed across peers, consistent with
  broad price discovery or continuation; the reversal remains UNRESOLVED.

Zero is the economic boundary between confirmation and nonconfirmation, not a
fitted magnitude threshold.  No PnL, future bar, symbol/session whitelist,
volatility cutoff, score multiplier or risk adjustment is used.
"""
from __future__ import annotations

from dataclasses import dataclass
from math import isfinite
import os
from typing import Any

from c10_v47_overlay import *  # noqa: F403 frozen lower-layer re-export
from c10_v47_overlay import __all__ as _LOWER_ALL


@dataclass(frozen=True, slots=True)
class PeerNonconfirmationDecision:
    approved: bool
    reason: str
    state: str
    peer_event_median: float | None
    event_direction_rank: int | None
    details: dict[str, Any]


def peer_nonconfirmation_router_enabled() -> bool:
    return os.environ.get("C10_V61_PEER_NONCONFIRMATION_ROUTER", "0") == "1"


def classify_peer_nonconfirmation(plan: Any) -> PeerNonconfirmationDecision:
    """Approve only rank-one FAR recovery not confirmed by the peer median."""

    enabled = peer_nonconfirmation_router_enabled()
    leadership_raw = getattr(plan, "details", {}).get("market_leadership", {})
    leadership = leadership_raw if isinstance(leadership_raw, dict) else {}
    rank_raw = leadership.get("event_direction_rank")
    median_raw = leadership.get("peer_event_median")
    try:
        rank = None if rank_raw is None else int(rank_raw)
    except (TypeError, ValueError):
        rank = None
    try:
        peer_median = None if median_raw is None else float(median_raw)
    except (TypeError, ValueError):
        peer_median = None

    common = {
        "schema": "candidate-10-v61-peer-nonconfirmation-v1",
        "enabled": enabled,
        "candidate_symbol": leadership.get("symbol"),
        "scenario": leadership.get("scenario"),
        "direction": leadership.get("direction"),
        "sweep_ts_ns": leadership.get("sweep_ts_ns"),
        "confirmation_ts_ns": leadership.get("confirmation_ts_ns"),
        "event_direction_rank": rank,
        "peer_event_median": peer_median,
        "candidate_event_move": leadership.get("candidate_event_move"),
        "peer_returns": dict(sorted(
            leadership.get("peer_returns", {}).items()
        )) if isinstance(leadership.get("peer_returns"), dict) else {},
        "state_contract": {
            "SMT_PEER_NONCONFIRMATION": (
                "candidate event direction rank equals one and the existing "
                "direction-signed peer median is nonpositive"
            ),
            "BROAD_MARKET_DIRECTIONAL_CONFIRMATION": (
                "candidate event direction rank equals one and the existing "
                "direction-signed peer median is positive"
            ),
        },
        "economic_boundary": 0.0,
        "new_fitted_thresholds": [],
        "not_used": [
            "future observations",
            "PnL or trade outcome",
            "return magnitude threshold",
            "symbol or session whitelist",
            "risk multiplier",
        ],
    }
    if not enabled:
        return PeerNonconfirmationDecision(
            approved=True,
            reason="PEER_NONCONFIRMATION_ROUTER_DISABLED",
            state="DISABLED",
            peer_event_median=peer_median,
            event_direction_rank=rank,
            details={**common, "applied": False, "selected_state": "DISABLED"},
        )
    if rank is None or peer_median is None or not isfinite(peer_median):
        return PeerNonconfirmationDecision(
            approved=False,
            reason="PEER_NONCONFIRMATION_INPUT_UNAVAILABLE",
            state="UNRESOLVED",
            peer_event_median=peer_median,
            event_direction_rank=rank,
            details={**common, "applied": True, "selected_state": "UNRESOLVED"},
        )
    if rank != 1:
        return PeerNonconfirmationDecision(
            approved=False,
            reason="CANDIDATE_NOT_EVENT_DIRECTION_LEADER",
            state="UNRESOLVED",
            peer_event_median=peer_median,
            event_direction_rank=rank,
            details={**common, "applied": True, "selected_state": "UNRESOLVED"},
        )
    if peer_median <= 0.0:
        state = "SMT_PEER_NONCONFIRMATION"
        return PeerNonconfirmationDecision(
            approved=True,
            reason="SMT_PEER_NONCONFIRMATION_CONFIRMED",
            state=state,
            peer_event_median=peer_median,
            event_direction_rank=rank,
            details={**common, "applied": True, "selected_state": state},
        )
    state = "BROAD_MARKET_DIRECTIONAL_CONFIRMATION"
    return PeerNonconfirmationDecision(
        approved=False,
        reason="BROAD_MARKET_DIRECTIONAL_CONFIRMATION",
        state=state,
        peer_event_median=peer_median,
        event_direction_rank=rank,
        details={**common, "applied": True, "selected_state": state},
    )


__all__ = [
    *_LOWER_ALL,
    "PeerNonconfirmationDecision",
    "classify_peer_nonconfirmation",
    "peer_nonconfirmation_router_enabled",
]
