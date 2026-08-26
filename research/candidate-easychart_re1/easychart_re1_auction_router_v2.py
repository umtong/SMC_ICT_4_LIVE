"""Remove the hidden macro gate from horizontal S/R-flip continuation.

The first integrated horizontal-flip family reused a complete policy bundle as
its signal source and then extracted horizontal acceptance plans.  That bundle
had already applied its own one-hour router before the mechanism-specific router
could see the plans.  A complete local break/hold/retest/response was therefore
still silently discarded when the slower macro label disagreed.

This module instantiates the existing horizontal-flip scenario engine directly.
The engine remains responsible for pre-existing horizontal structure, body
break, next-bar external hold, first return, structural stop and original
objective.  The wrapper remains responsible for the first completed micro
response and significant objective refinement.  Only after that complete local
auction does the integrated router apply the intended active-opposing-common-
factor veto.  Rejection routing and every execution/account rule are unchanged.
"""
from __future__ import annotations

from typing import Any

import contracts_v5 as _contracts
from easychart_re1_auction_router_fvg import (
    LOCAL_FVG_CONTINUATION_RULE,
    EasyChartRE1AuctionRouterFVGBundle,
)
from easychart_re1_complete_policy import LocatedHorizontalFlipEngine
from easychart_re1_horizontal_flip_response import (
    HORIZONTAL_FLIP_RESPONSE_RULE,
    HorizontalFlipResponseFamily,
)
from easychart_re1_local_auction_continuation import EasyChartRE1LocalAuctionStrategy


DIRECT_HORIZONTAL_FLIP_ENGINE_RULE = (
    "RESEARCH_IMPLEMENTATION:"
    "HORIZONTAL_FLIP_ENGINE_EMITS_RAW_LOCAL_ACCEPTANCE_BEFORE_THE_MECHANISM_SPECIFIC_CONTEXT_ROUTER"
)
if DIRECT_HORIZONTAL_FLIP_ENGINE_RULE not in _contracts.RESEARCH_RULES:
    _contracts.RESEARCH_RULES += (DIRECT_HORIZONTAL_FLIP_ENGINE_RULE,)


class DirectHorizontalFlipResponseFamily(HorizontalFlipResponseFamily):
    """Use the existing local flip state machine without a nested macro router."""

    def __init__(self, symbol: str, tick_size: float, minimum_gross_rr: float = 1.0) -> None:
        super().__init__(symbol, tick_size, minimum_gross_rr)
        self.source = LocatedHorizontalFlipEngine(
            symbol,
            tick_size,
            scale_name="HORIZONTAL_FLIP",
            higher_minutes=15,
            decision_minutes=5,
            trigger_minutes=1,
            minimum_gross_rr=minimum_gross_rr,
        )

    @property
    def diagnostics(self) -> dict[str, Any]:
        output = dict(super().diagnostics)
        output["raw_source_before_context_router"] = True
        output["direct_source_rule"] = DIRECT_HORIZONTAL_FLIP_ENGINE_RULE
        return output


class EasyChartRE1AuctionRouterV2Bundle(EasyChartRE1AuctionRouterFVGBundle):
    """Integrated router with raw local horizontal acceptance ownership."""

    def __init__(self, symbol: str, tick_size: float, minimum_gross_rr: float = 1.0) -> None:
        super().__init__(symbol, tick_size, minimum_gross_rr)
        self.horizontal_flip_response = DirectHorizontalFlipResponseFamily(
            symbol,
            tick_size,
            minimum_gross_rr,
        )

    @property
    def diagnostics(self) -> dict[str, Any]:
        output = dict(super().diagnostics)
        output["direct_horizontal_flip_source"] = {
            "rule_provenance": DIRECT_HORIZONTAL_FLIP_ENGINE_RULE,
            "first_response_rule": HORIZONTAL_FLIP_RESPONSE_RULE,
            "local_fvg_rule": LOCAL_FVG_CONTINUATION_RULE,
        }
        return output


MultiScaleScenarioBundle = EasyChartRE1AuctionRouterV2Bundle
StrategyClass = EasyChartRE1LocalAuctionStrategy
