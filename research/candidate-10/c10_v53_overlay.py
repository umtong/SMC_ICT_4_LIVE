"""v53 cross-sectional extreme-state router over the v47 event leader.

External lead-lag, shock-propagation and SMT-divergence ideas imply that a
single-instrument source-liquidity raid is interpretable only relative to
correlated markets.  This layer introduces no return threshold.  It separates
two complete ordinal states at the sweep:

``RELATIVE_STRENGTH_RESILIENCE``
    The candidate was already rank one in the proposed direction over the
    frozen trailing window, suffered a local source-liquidity raid, and is again
    event rank one from sweep to confirmation.  The local raid did not dislodge
    the cross-market leader.

``CROSS_SECTIONAL_RANK_REVERSAL``
    The candidate was last in the proposed direction at the sweep, then became
    event rank one through confirmation.  This is the mechanical analogue of an
    SMT-style isolated extreme followed by a confirmed leadership handoff.

Ranks between those endpoints are explicitly ``UNRESOLVED``.  The terminal rank
is derived from the synchronized directional-return map, so the rule is portable
to a different peer count and contains no symbol/session whitelist, PnL, future
bar, fitted magnitude threshold or risk multiplier.
"""
from __future__ import annotations

from dataclasses import dataclass
import os
from typing import Any

from c10_v47_overlay import *  # noqa: F403 frozen lower-layer re-export
from c10_v47_overlay import __all__ as _LOWER_ALL


@dataclass(frozen=True, slots=True)
class ExtremeStateDecision:
    approved: bool
    reason: str
    state: str
    trailing_direction_rank: int | None
    event_direction_rank: int | None
    market_count: int
    details: dict[str, Any]


def extreme_state_router_enabled() -> bool:
    return os.environ.get("C10_V53_EXTREME_STATE_ROUTER", "0") == "1"


def _rank(leadership: dict[str, Any], name: str) -> int | None:
    raw = leadership.get(name)
    try:
        return None if raw is None else int(raw)
    except (TypeError, ValueError):
        return None


def classify_cross_sectional_extreme_state(plan: Any) -> ExtremeStateDecision:
    """Approve endpoint rank states after the v47 event-rank-one decision."""

    enabled = extreme_state_router_enabled()
    leadership_raw = getattr(plan, "details", {}).get("market_leadership", {})
    leadership = leadership_raw if isinstance(leadership_raw, dict) else {}
    directional_raw = leadership.get("directional_returns", {})
    directional = directional_raw if isinstance(directional_raw, dict) else {}
    market_count = len(directional)
    trailing_rank = _rank(leadership, "trailing_direction_rank")
    event_rank = _rank(leadership, "event_direction_rank")

    common = {
        "schema": "candidate-10-v53-cross-sectional-extreme-state-v1",
        "enabled": enabled,
        "candidate_symbol": leadership.get("symbol"),
        "scenario": leadership.get("scenario"),
        "direction": leadership.get("direction"),
        "sweep_ts_ns": leadership.get("sweep_ts_ns"),
        "confirmation_ts_ns": leadership.get("confirmation_ts_ns"),
        "market_count": market_count,
        "trailing_direction_rank": trailing_rank,
        "event_direction_rank": event_rank,
        "directional_returns": dict(sorted(directional.items())),
        "candidate_event_move": leadership.get("candidate_event_move"),
        "peer_event_median": leadership.get("peer_event_median"),
        "event_path_efficiency": leadership.get("event_path_efficiency"),
        "event_standardized_displacement": leadership.get(
            "event_standardized_displacement"
        ),
        "confirmation_impulse": leadership.get("confirmation_impulse"),
        "state_contract": {
            "RELATIVE_STRENGTH_RESILIENCE": (
                "trailing direction rank equals one and event direction rank "
                "equals one"
            ),
            "CROSS_SECTIONAL_RANK_REVERSAL": (
                "trailing direction rank equals synchronized market count and "
                "event direction rank equals one"
            ),
            "UNRESOLVED": "all interior trailing ranks",
        },
        "new_fitted_thresholds": [],
        "not_used": [
            "future observations",
            "PnL or trade outcome",
            "fixed return or volatility threshold",
            "symbol or session whitelist",
            "risk multiplier",
        ],
    }
    if not enabled:
        return ExtremeStateDecision(
            approved=True,
            reason="EXTREME_STATE_ROUTER_DISABLED",
            state="DISABLED",
            trailing_direction_rank=trailing_rank,
            event_direction_rank=event_rank,
            market_count=market_count,
            details={**common, "applied": False, "selected_state": "DISABLED"},
        )
    if market_count < 3:
        return ExtremeStateDecision(
            approved=False,
            reason="INSUFFICIENT_SYNCHRONIZED_MARKETS",
            state="UNRESOLVED",
            trailing_direction_rank=trailing_rank,
            event_direction_rank=event_rank,
            market_count=market_count,
            details={**common, "applied": True, "selected_state": "UNRESOLVED"},
        )
    if trailing_rank is None or event_rank is None:
        return ExtremeStateDecision(
            approved=False,
            reason="CROSS_SECTIONAL_RANK_UNAVAILABLE",
            state="UNRESOLVED",
            trailing_direction_rank=trailing_rank,
            event_direction_rank=event_rank,
            market_count=market_count,
            details={**common, "applied": True, "selected_state": "UNRESOLVED"},
        )
    if not (1 <= trailing_rank <= market_count and 1 <= event_rank <= market_count):
        return ExtremeStateDecision(
            approved=False,
            reason="INVALID_CROSS_SECTIONAL_RANK",
            state="UNRESOLVED",
            trailing_direction_rank=trailing_rank,
            event_direction_rank=event_rank,
            market_count=market_count,
            details={**common, "applied": True, "selected_state": "UNRESOLVED"},
        )
    if event_rank != 1:
        return ExtremeStateDecision(
            approved=False,
            reason="CANDIDATE_NOT_EVENT_DIRECTION_LEADER",
            state="UNRESOLVED",
            trailing_direction_rank=trailing_rank,
            event_direction_rank=event_rank,
            market_count=market_count,
            details={**common, "applied": True, "selected_state": "UNRESOLVED"},
        )
    if trailing_rank == 1:
        state = "RELATIVE_STRENGTH_RESILIENCE"
        return ExtremeStateDecision(
            approved=True,
            reason="EXTREME_STATE_RELATIVE_STRENGTH_RESILIENCE",
            state=state,
            trailing_direction_rank=trailing_rank,
            event_direction_rank=event_rank,
            market_count=market_count,
            details={**common, "applied": True, "selected_state": state},
        )
    if trailing_rank == market_count:
        state = "CROSS_SECTIONAL_RANK_REVERSAL"
        return ExtremeStateDecision(
            approved=True,
            reason="EXTREME_STATE_CROSS_SECTIONAL_RANK_REVERSAL",
            state=state,
            trailing_direction_rank=trailing_rank,
            event_direction_rank=event_rank,
            market_count=market_count,
            details={**common, "applied": True, "selected_state": state},
        )
    return ExtremeStateDecision(
        approved=False,
        reason="AMBIGUOUS_INTERIOR_CROSS_SECTIONAL_STATE",
        state="UNRESOLVED",
        trailing_direction_rank=trailing_rank,
        event_direction_rank=event_rank,
        market_count=market_count,
        details={**common, "applied": True, "selected_state": "UNRESOLVED"},
    )


__all__ = [
    *_LOWER_ALL,
    "ExtremeStateDecision",
    "classify_cross_sectional_extreme_state",
    "extreme_state_router_enabled",
]
