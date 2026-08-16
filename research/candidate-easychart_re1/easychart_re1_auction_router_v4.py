"""Give structure-local owners priority over generic diagonal acceptance.

The v3 family set was coherent, but its call order let a generic mature diagonal
acceptance claim a causal episode before a more specific horizontal flip or
flow-validated OB/FVG continuation from the same completed bar.  That is an
implementation ownership error, not a market rule.

This final ordering processes the established integrated core first:

1. rejection at its responsible structure;
2. event-local OB/FVG continuation;
3. horizontal S/R flip;
4. mature diagonal/channel acceptance only for the residual unclaimed episode.

The diagonal family remains fully available when it represents an independent
auction.  No signal threshold, target, stop, risk, account or execution rule is
changed.
"""
from __future__ import annotations

from typing import Any

import contracts_v5 as _contracts
from contracts_v5 import V5TradePlan
from domain import Candle
from easychart_re1_auction_router_v3 import (
    MATURE_DIAGONAL_ACCEPTANCE_RULE,
    EasyChartRE1AuctionRouterV3Bundle,
)
from easychart_re1_local_auction_continuation import EasyChartRE1LocalAuctionStrategy


SPECIFIC_ENTRY_OWNER_PRIORITY_RULE = (
    "SOURCE_AMBIGUITY_TRANSLATION:"
    "RESPONSIBLE_REJECTION_EVENT_LOCAL_OB_FVG_AND_HORIZONTAL_FLIP_OWN_AN_OVERLAPPING_EPISODE_BEFORE_GENERIC_DIAGONAL_ACCEPTANCE"
)
if SPECIFIC_ENTRY_OWNER_PRIORITY_RULE not in _contracts.TRANSLATION_RULES:
    _contracts.TRANSLATION_RULES += (SPECIFIC_ENTRY_OWNER_PRIORITY_RULE,)


class EasyChartRE1AuctionRouterV4Bundle(EasyChartRE1AuctionRouterV3Bundle):
    """Same mechanisms as v3 with deterministic evidence ownership."""

    def on_bar(self, timeframe_minutes: int, bar: Candle) -> list[V5TradePlan]:
        # Bypass v3.on_bar so its diagonal-first order cannot claim the episode.
        core = super(EasyChartRE1AuctionRouterV3Bundle, self).on_bar(
            timeframe_minutes,
            bar,
        )
        diagonal = self._route_diagonal(
            self.mature_diagonal_acceptance.on_bar(timeframe_minutes, bar)
        )
        return sorted(
            core + diagonal,
            key=lambda plan: (
                plan.interaction_time_ns,
                -plan.higher_timeframe_minutes,
                plan.symbol,
                plan.plan_id,
            ),
        )

    @property
    def diagnostics(self) -> dict[str, Any]:
        output = dict(super().diagnostics)
        output["specific_entry_owner_priority"] = {
            "order": (
                "REJECTION",
                "EVENT_LOCAL_OB_FVG_CONTINUATION",
                "HORIZONTAL_SR_FLIP",
                "RESIDUAL_MATURE_DIAGONAL_ACCEPTANCE",
            ),
            "rules": (
                SPECIFIC_ENTRY_OWNER_PRIORITY_RULE,
                MATURE_DIAGONAL_ACCEPTANCE_RULE,
            ),
        }
        return output


MultiScaleScenarioBundle = EasyChartRE1AuctionRouterV4Bundle
StrategyClass = EasyChartRE1LocalAuctionStrategy
