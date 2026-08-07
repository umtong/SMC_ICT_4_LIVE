"""v28 resolved-auction certificate over the frozen v27 cost model.

This changes one causal variable only: a FAR may trade only after the proposed
reversal direction is no longer opposed by an unresolved market-wide auction.
Follower relative-recovery approvals must also contain the same local impulse
minimum already required by the unanimous-peer branch.  No threshold is fitted;
the frozen Candidate 11 semantic thresholds are reused verbatim.
"""
from __future__ import annotations

from dataclasses import replace
from statistics import median
import os
from typing import Any

from c10_v27_overlay import (  # re-exported for the deterministic source patch
    CostAwareRiskSizer,
    LiveImpactLedger,
    apply_cost_overlay,
)


class ResolvedLeadershipGateAdapter:
    def __init__(self, original: Any, *, ablated: bool) -> None:
        self.original = original
        self.ablated = bool(ablated)

    def observe_batch(self, *args: Any, **kwargs: Any) -> Any:
        return self.original.observe_batch(*args, **kwargs)

    def decide(self, *args: Any, **kwargs: Any) -> Any:
        decision = self.original.decide(*args, **kwargs)
        if self.ablated or not decision.approved or decision.scenario != "FAR":
            return decision

        scores = decision.directional_trend_scores
        candidate_score = scores.get(decision.symbol)
        if candidate_score is None or not scores:
            return replace(
                decision,
                approved=False,
                reason="RESOLUTION_CERTIFICATE_MISSING_TREND_STATE",
            )
        market_score = median(scores.values())
        severe = float(self.original.severe_adverse_trend_score)
        if candidate_score <= severe and market_score <= severe:
            return replace(
                decision,
                approved=False,
                reason="UNRESOLVED_MARKET_WIDE_ADVERSE_AUCTION",
            )

        if (
            decision.symbol != decision.leader
            and decision.reason == "FOLLOWER_FAR_DIRECTIONAL_LEADER_RECOVERY"
            and (
                decision.confirmation_impulse is None
                or decision.confirmation_impulse
                < float(self.original.minimum_follower_confirmation_impulse)
            )
        ):
            return replace(
                decision,
                approved=False,
                reason="FOLLOWER_FAR_WEAK_LOCAL_DISPLACEMENT",
            )
        return replace(
            decision,
            reason=f"RESOLVED_{decision.reason}",
        )


def build_leadership_gate(
    gate_type: type,
    symbols: tuple[str, ...],
    *,
    lookback_bars: int,
) -> ResolvedLeadershipGateAdapter:
    return ResolvedLeadershipGateAdapter(
        gate_type(symbols, lookback_bars=lookback_bars),
        ablated=os.environ.get("C10_V28_ABLATE_RESOLUTION", "0") == "1",
    )
