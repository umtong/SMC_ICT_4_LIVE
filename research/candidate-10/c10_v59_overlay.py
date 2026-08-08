"""v59 true pre-event reversal ownership for failed auctions.

A failed auction must reverse the candidate's own preceding directional auction.
The existing Candidate 11 direction-signed trailing trend score already answers
that question: it must be negative before a trade in the proposed reversal
direction can be owned.

Unlike v56, this layer does not require a cross-market trailing rank.  Controlled
attribution showed that rank removed valid leader/follower reversal winners,
whereas the sign condition alone preserved all observed winners and removed the
same-direction continuation mislabeled as FAR.  No magnitude threshold, PnL,
future observation, symbol whitelist or risk multiplier is introduced.
"""
from __future__ import annotations

from dataclasses import dataclass
import os
from typing import Any

from c10_v52_overlay import *  # noqa: F403 frozen lower-layer re-export
from c10_v52_overlay import __all__ as _LOWER_ALL


@dataclass(frozen=True, slots=True)
class TrueReversalDecision:
    approved: bool
    reason: str
    details: dict[str, Any]


def true_reversal_enabled() -> bool:
    return os.environ.get("C10_V59_TRUE_REVERSAL", "0") == "1"


def classify_true_reversal(plan: Any) -> TrueReversalDecision:
    enabled = true_reversal_enabled()
    leadership_raw = getattr(plan, "details", {}).get("market_leadership", {})
    leadership = leadership_raw if isinstance(leadership_raw, dict) else {}
    symbol = str(leadership.get("symbol", ""))
    scenario = str(leadership.get("scenario", ""))
    trends_raw = leadership.get("directional_trend_scores", {})
    trends = trends_raw if isinstance(trends_raw, dict) else {}
    trend_raw = trends.get(symbol)
    try:
        trend = None if trend_raw is None else float(trend_raw)
    except (TypeError, ValueError):
        trend = None
    common = {
        "schema": "candidate-10-v59-true-pre-event-reversal-v1",
        "enabled": enabled,
        "symbol": symbol,
        "scenario": scenario,
        "candidate_trailing_directional_trend_score": trend,
        "contract": (
            "the proposed FAR direction must oppose the candidate's completed "
            "pre-event direction-signed trailing auction"
        ),
        "approval_predicate": (
            "candidate_trailing_directional_trend_score < 0"
        ),
        "not_used": [
            "cross-market trailing rank",
            "future observations",
            "PnL or trade outcome",
            "new fitted magnitude threshold",
            "symbol whitelist",
            "risk multiplier",
        ],
        "new_fitted_thresholds": [],
    }
    if not enabled:
        return TrueReversalDecision(
            True,
            "TRUE_REVERSAL_DISABLED",
            {**common, "applied": False},
        )
    if scenario != "FAR":
        return TrueReversalDecision(
            False,
            "TRUE_REVERSAL_REQUIRES_FAR",
            {**common, "applied": True},
        )
    if trend is None:
        return TrueReversalDecision(
            False,
            "TRUE_REVERSAL_INPUT_UNAVAILABLE",
            {**common, "applied": True},
        )
    if trend >= 0.0:
        return TrueReversalDecision(
            False,
            "FAILED_AUCTION_EXTENDS_CANDIDATE_TRAILING_AUCTION",
            {**common, "applied": True},
        )
    return TrueReversalDecision(
        True,
        "TRUE_PRE_EVENT_REVERSAL_CONFIRMED",
        {**common, "applied": True},
    )


__all__ = [
    *_LOWER_ALL,
    "TrueReversalDecision",
    "classify_true_reversal",
    "true_reversal_enabled",
]
