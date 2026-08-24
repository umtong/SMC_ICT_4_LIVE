"""Nautilus execution binding for the single latent-liquidity episode policy."""
from __future__ import annotations

from typing import Any

from easychart_re1_local_auction_continuation import (
    EasyChartRE1LocalAuctionStrategy,
)


class EasyChartRE1LatentAuctionStrategy(EasyChartRE1LocalAuctionStrategy):
    """Propagate four-market state and claim each causal event once.

    The latent bundle itself decides whether common initiative supports or
    contradicts a boundary event.  The inherited factor router is therefore an
    observer here, not a second independent hard filter.
    """

    def __init__(self, config: Any) -> None:
        super().__init__(config)
        self.claimed_liquidity_events: set[str] = set()

    def _factor_allows(self, plan: Any) -> bool:
        return True

    def _submit_plan(self, instrument_id: Any, plan: Any) -> bool:
        event_id = str(getattr(plan, "causal_event_id", ""))
        if not event_id:
            raise RuntimeError("latent auction plan is missing causal_event_id")
        if event_id in self.claimed_liquidity_events:
            self._record(
                "liquidity_episode_rejected_duplicate",
                plan_id=plan.plan_id,
                causal_event_id=event_id,
                instrument_id=str(instrument_id),
            )
            return False
        submitted = super()._submit_plan(instrument_id, plan)
        if submitted:
            self.claimed_liquidity_events.add(event_id)
            self._record(
                "liquidity_episode_claimed",
                plan_id=plan.plan_id,
                causal_event_id=event_id,
                instrument_id=str(instrument_id),
            )
        return submitted


StrategyClass = EasyChartRE1LatentAuctionStrategy

