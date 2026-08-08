"""v62 isolated cross-market extreme-transfer state over v47 event leadership.

This candidate fuses two externally sourced but distinct observations into one
complete latent market state rather than stacking arbitrary filters:

* SMT-style nonconfirmation: the direction-signed median move of correlated
  peers from source sweep to confirmation is nonpositive, so the proposed
  reversal has not propagated broadly across the peer set;
* cross-sectional role transition: the candidate was either already the
  direction leader before the raid or was the last-ranked market and became the
  event leader after the raid.

The resulting states are:

``ISOLATED_LEADER_RESILIENCE``
    trailing direction rank 1 -> event direction rank 1, with nonpositive peer
    median.  A local raid failed to dislodge the pre-existing relative-strength
    leader and peers did not broadly confirm the recovery.

``ISOLATED_LAST_TO_FIRST_REVERSAL``
    trailing last rank -> event direction rank 1, with nonpositive peer median.
    The sweeping market completed a cross-sectional role reversal while peers
    still failed to confirm the proposed direction.

Every interior rank or positive peer median is ``UNRESOLVED``.  Zero is only the
sign boundary between peer confirmation and nonconfirmation.  Terminal rank is
derived from the synchronized market count.  No outcome, future observation,
return magnitude threshold, symbol/session whitelist or risk multiplier is used.
"""
from __future__ import annotations

from dataclasses import dataclass
from math import isfinite
import os
from typing import Any

from c10_v47_overlay import *  # noqa: F403 frozen lower-layer re-export
from c10_v47_overlay import __all__ as _LOWER_ALL


@dataclass(frozen=True, slots=True)
class IsolatedExtremeTransferDecision:
    approved: bool
    reason: str
    state: str
    trailing_direction_rank: int | None
    event_direction_rank: int | None
    market_count: int
    peer_event_median: float | None
    details: dict[str, Any]


def isolated_extreme_transfer_enabled() -> bool:
    return os.environ.get("C10_V62_ISOLATED_EXTREME_TRANSFER", "0") == "1"


def _integer(value: Any) -> int | None:
    try:
        return None if value is None else int(value)
    except (TypeError, ValueError):
        return None


def _number(value: Any) -> float | None:
    try:
        result = None if value is None else float(value)
    except (TypeError, ValueError):
        return None
    return result if result is None or isfinite(result) else None


def classify_isolated_extreme_transfer(
    plan: Any,
) -> IsolatedExtremeTransferDecision:
    """Approve only a complete isolated endpoint role-transition state."""

    enabled = isolated_extreme_transfer_enabled()
    leadership_raw = getattr(plan, "details", {}).get("market_leadership", {})
    leadership = leadership_raw if isinstance(leadership_raw, dict) else {}
    directional_raw = leadership.get("directional_returns", {})
    directional = directional_raw if isinstance(directional_raw, dict) else {}
    market_count = len(directional)
    trailing_rank = _integer(leadership.get("trailing_direction_rank"))
    event_rank = _integer(leadership.get("event_direction_rank"))
    peer_median = _number(leadership.get("peer_event_median"))

    common = {
        "schema": "candidate-10-v62-isolated-extreme-transfer-v1",
        "enabled": enabled,
        "candidate_symbol": leadership.get("symbol"),
        "scenario": leadership.get("scenario"),
        "direction": leadership.get("direction"),
        "sweep_ts_ns": leadership.get("sweep_ts_ns"),
        "confirmation_ts_ns": leadership.get("confirmation_ts_ns"),
        "market_count": market_count,
        "trailing_direction_rank": trailing_rank,
        "event_direction_rank": event_rank,
        "peer_event_median": peer_median,
        "directional_returns": dict(sorted(directional.items())),
        "peer_returns": dict(sorted(
            leadership.get("peer_returns", {}).items()
        )) if isinstance(leadership.get("peer_returns"), dict) else {},
        "candidate_event_move": leadership.get("candidate_event_move"),
        "event_path_efficiency": leadership.get("event_path_efficiency"),
        "event_standardized_displacement": leadership.get(
            "event_standardized_displacement"
        ),
        "confirmation_impulse": leadership.get("confirmation_impulse"),
        "state_contract": {
            "ISOLATED_LEADER_RESILIENCE": (
                "trailing rank one, event rank one, peer median nonpositive"
            ),
            "ISOLATED_LAST_TO_FIRST_REVERSAL": (
                "trailing rank equals synchronized market count, event rank "
                "one, peer median nonpositive"
            ),
            "UNRESOLVED": (
                "positive peer median, interior trailing rank, or unavailable "
                "synchronized state"
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

    def decision(
        approved: bool,
        reason: str,
        state: str,
        *,
        applied: bool = True,
    ) -> IsolatedExtremeTransferDecision:
        return IsolatedExtremeTransferDecision(
            approved=approved,
            reason=reason,
            state=state,
            trailing_direction_rank=trailing_rank,
            event_direction_rank=event_rank,
            market_count=market_count,
            peer_event_median=peer_median,
            details={**common, "applied": applied, "selected_state": state},
        )

    if not enabled:
        return decision(
            True,
            "ISOLATED_EXTREME_TRANSFER_DISABLED",
            "DISABLED",
            applied=False,
        )
    if market_count < 3:
        return decision(False, "INSUFFICIENT_SYNCHRONIZED_MARKETS", "UNRESOLVED")
    if trailing_rank is None or event_rank is None or peer_median is None:
        return decision(False, "ISOLATED_TRANSFER_INPUT_UNAVAILABLE", "UNRESOLVED")
    if not (1 <= trailing_rank <= market_count and 1 <= event_rank <= market_count):
        return decision(False, "INVALID_CROSS_SECTIONAL_RANK", "UNRESOLVED")
    if event_rank != 1:
        return decision(False, "CANDIDATE_NOT_EVENT_DIRECTION_LEADER", "UNRESOLVED")
    if peer_median > 0.0:
        return decision(
            False,
            "BROAD_MARKET_DIRECTIONAL_CONFIRMATION",
            "BROAD_MARKET_DIRECTIONAL_CONFIRMATION",
        )
    if trailing_rank == 1:
        return decision(
            True,
            "ISOLATED_LEADER_RESILIENCE_CONFIRMED",
            "ISOLATED_LEADER_RESILIENCE",
        )
    if trailing_rank == market_count:
        return decision(
            True,
            "ISOLATED_LAST_TO_FIRST_REVERSAL_CONFIRMED",
            "ISOLATED_LAST_TO_FIRST_REVERSAL",
        )
    return decision(
        False,
        "AMBIGUOUS_INTERIOR_ISOLATED_STATE",
        "UNRESOLVED",
    )


__all__ = [
    *_LOWER_ALL,
    "IsolatedExtremeTransferDecision",
    "classify_isolated_extreme_transfer",
    "isolated_extreme_transfer_enabled",
]
