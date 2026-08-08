"""v56 pre-event reversal ownership for funded failed-auction runners.

A failed auction must reverse the candidate's own preceding directional auction,
not merely rank first during the short sweep-to-confirmation interval while
continuing its prior drift.  It also cannot be the weakest trailing member of the
synchronized four-market set and still claim reversal leadership.

This layer uses only two predicates already present in Candidate 11:

* candidate trailing direction-signed trend score is negative;
* candidate trailing directional rank is in the existing top half.

No magnitude threshold, PnL, future observation, symbol whitelist or risk
multiplier is introduced.
"""
from __future__ import annotations

from dataclasses import dataclass
import os
from typing import Any

from c10_v52_overlay import *  # noqa: F403 frozen lower-layer re-export
from c10_v52_overlay import __all__ as _LOWER_ALL


@dataclass(frozen=True, slots=True)
class ReversalOwnershipDecision:
    approved: bool
    reason: str
    details: dict[str, Any]


def reversal_ownership_enabled() -> bool:
    return os.environ.get("C10_V56_REVERSAL_OWNERSHIP", "0") == "1"


def classify_reversal_ownership(plan: Any) -> ReversalOwnershipDecision:
    enabled = reversal_ownership_enabled()
    leadership_raw = getattr(plan, "details", {}).get("market_leadership", {})
    leadership = leadership_raw if isinstance(leadership_raw, dict) else {}
    symbol = str(leadership.get("symbol", ""))
    scenario = str(leadership.get("scenario", ""))
    returns_raw = leadership.get("directional_returns", {})
    returns = returns_raw if isinstance(returns_raw, dict) else {}
    trends_raw = leadership.get("directional_trend_scores", {})
    trends = trends_raw if isinstance(trends_raw, dict) else {}
    trend_raw = trends.get(symbol)
    rank_raw = leadership.get("trailing_direction_rank")
    try:
        trend = None if trend_raw is None else float(trend_raw)
        rank = None if rank_raw is None else int(rank_raw)
    except (TypeError, ValueError):
        trend, rank = None, None
    market_count = len(returns)
    top_half_limit = max(1, (market_count + 1) // 2) if market_count else None
    common = {
        "schema": "candidate-10-v56-pre-event-reversal-ownership-v1",
        "enabled": enabled,
        "symbol": symbol,
        "scenario": scenario,
        "candidate_trailing_directional_trend_score": trend,
        "trailing_direction_rank": rank,
        "synchronized_market_count": market_count,
        "existing_top_half_limit": top_half_limit,
        "contract": {
            "true_reversal": (
                "candidate trailing direction-signed trend score is negative"
            ),
            "reversal_ownership": (
                "candidate trailing directional rank is in the existing top half"
            ),
        },
        "not_used": [
            "future observations",
            "PnL or trade outcome",
            "new fitted magnitude threshold",
            "symbol whitelist",
            "risk multiplier",
        ],
        "new_fitted_thresholds": [],
    }
    if not enabled:
        return ReversalOwnershipDecision(
            True,
            "REVERSAL_OWNERSHIP_DISABLED",
            {**common, "applied": False},
        )
    if scenario != "FAR":
        return ReversalOwnershipDecision(
            False,
            "REVERSAL_OWNERSHIP_REQUIRES_FAR",
            {**common, "applied": True},
        )
    if trend is None or rank is None or top_half_limit is None:
        return ReversalOwnershipDecision(
            False,
            "REVERSAL_OWNERSHIP_INPUT_UNAVAILABLE",
            {**common, "applied": True},
        )
    if trend >= 0.0:
        return ReversalOwnershipDecision(
            False,
            "FAILED_AUCTION_DOES_NOT_REVERSE_CANDIDATE_TRAILING_AUCTION",
            {**common, "applied": True},
        )
    if rank > top_half_limit:
        return ReversalOwnershipDecision(
            False,
            "FAILED_AUCTION_CANDIDATE_LACKS_TRAILING_REVERSAL_OWNERSHIP",
            {**common, "applied": True},
        )
    return ReversalOwnershipDecision(
        True,
        "PRE_EVENT_REVERSAL_OWNERSHIP_CONFIRMED",
        {**common, "applied": True},
    )


__all__ = [
    *_LOWER_ALL,
    "ReversalOwnershipDecision",
    "classify_reversal_ownership",
    "reversal_ownership_enabled",
]
