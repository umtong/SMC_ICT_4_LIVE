"""Pure causal quality router for Candidate 18 reversal initiatives.

A displayed-liquidity failed auction is not enough. The later opposite
initiative must either survive the complete observation window or arrive as an
above-baseline one-bar shock. Middle-window confirmations are unresolved: they
are neither the persistence proof nor the shock state.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import math


class InitiativeRoute(StrEnum):
    SUSTAINED = "SUSTAINED"
    SHOCK = "SHOCK"
    UNRESOLVED = "UNRESOLVED"


@dataclass(frozen=True, slots=True)
class InitiativeQuality:
    route: InitiativeRoute
    reason: str


def classify_initiative_quality(
    *,
    observations: int,
    max_wait_bars: int,
    notional_burst: float,
    shock_burst_min: float = 1.0,
) -> InitiativeQuality:
    """Classify a causally confirmed initiative without consulting PnL.

    ``notional_burst`` is already normalized by the rolling baseline used by
    the shared feature builder. A value above one therefore means the first
    completed initiative bar carried above-baseline traded notional.
    """
    if observations <= 0:
        raise ValueError("observations must be positive")
    if max_wait_bars <= 0:
        raise ValueError("max_wait_bars must be positive")
    if observations > max_wait_bars:
        raise ValueError("observations cannot exceed max_wait_bars")
    if not math.isfinite(shock_burst_min) or shock_burst_min < 1.0:
        raise ValueError("shock_burst_min must be finite and at least baseline")

    if observations == max_wait_bars:
        return InitiativeQuality(
            InitiativeRoute.SUSTAINED,
            "INITIATIVE_SURVIVED_COMPLETE_CAUSAL_OBSERVATION_WINDOW",
        )
    if (
        observations == 1
        and math.isfinite(notional_burst)
        and notional_burst > shock_burst_min
    ):
        return InitiativeQuality(
            InitiativeRoute.SHOCK,
            "FIRST_BAR_INITIATIVE_CARRIED_ABOVE_BASELINE_NOTIONAL_SHOCK",
        )
    return InitiativeQuality(
        InitiativeRoute.UNRESOLVED,
        "INITIATIVE_NEITHER_PERSISTENT_NOR_IMMEDIATE_NOTIONAL_SHOCK",
    )


__all__ = [
    "InitiativeQuality",
    "InitiativeRoute",
    "classify_initiative_quality",
]
