"""Pure quality-first arbitration helpers for EasyChart RE1 ML3."""
from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any, Iterable


@dataclass(frozen=True, slots=True)
class ScoredPlan:
    instrument_id: Any
    plan: Any
    target_first_probability: float
    target_net_r: float
    stop_net_r: float
    expected_net_r: float

    def __post_init__(self) -> None:
        values = (
            self.target_first_probability,
            self.target_net_r,
            self.stop_net_r,
            self.expected_net_r,
        )
        if not all(math.isfinite(value) for value in values):
            raise ValueError("scored plan contains non-finite values")
        if not 0.0 <= self.target_first_probability <= 1.0:
            raise ValueError("invalid target-first probability")


def deterministic_tie_break(plan: Any) -> tuple[Any, ...]:
    """The pre-ML structure-first order retained after quality and utility."""
    return (
        int(plan.interaction_time_ns),
        -int(plan.higher_timeframe_minutes),
        int(plan.setup_observed_time_ns),
        str(plan.symbol),
        str(plan.plan_id),
    )


def rank_scored_plans(candidates: Iterable[ScoredPlan]) -> list[ScoredPlan]:
    """Target-first probability first; post-cost utility breaks quality ties.

    A larger reward is not allowed to buy priority over a plan which is more
    likely to win.  Expected account-risk R remains useful once win likelihood
    is equal or nearly equal, followed by the original deterministic order.
    """
    return sorted(
        candidates,
        key=lambda item: (
            -item.target_first_probability,
            -item.expected_net_r,
            *deterministic_tie_break(item.plan),
        ),
    )


__all__ = ["ScoredPlan", "deterministic_tie_break", "rank_scored_plans"]
