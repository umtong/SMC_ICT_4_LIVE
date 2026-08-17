"""Executable complete EasyChart policy with local-engine timeframe contracts.

The integrated complete bundle receives 60m, 15m, 5m and 1m bars.  Its direct
horizontal and mature-diagonal sources are intentionally local 15m/5m/1m
auctions.  They must not receive the broad 60m context bar.  This wrapper keeps
the policy logic unchanged and replaces only the mature-diagonal instance with
a contract-preserving adapter.  The horizontal adapter is fixed in
``easychart_re1_auction_router_v2``.
"""
from __future__ import annotations

from typing import Any

from contracts_v5 import V5TradePlan
from domain import Candle
from easychart_re1_auction_router_v3 import MatureDiagonalResponseFamily
from easychart_re1_complete_bot_policy_v2 import EasyChartRE1CompleteBotPolicyV2Bundle
from easychart_re1_local_auction_continuation import EasyChartRE1LocalAuctionStrategy


LOCAL_ENGINE_TIMEFRAME_POLICY = (
    "IMPLEMENTATION_VALIDITY:DIRECT_HORIZONTAL_AND_MATURE_DIAGONAL_ENGINES_"
    "CONSUME_ONLY_THEIR_DECLARED_15M_5M_1M_STACK;60M_REMAINS_BROAD_CONTEXT"
)


class ContractedMatureDiagonalResponseFamily(MatureDiagonalResponseFamily):
    """Do not forward the parent bundle's 60m context bar into a 15/5/1 engine."""

    SUPPORTED_TIMEFRAMES = frozenset((15, 5, 1))

    def on_bar(self, timeframe_minutes: int, bar: Candle) -> list[V5TradePlan]:
        if timeframe_minutes not in self.SUPPORTED_TIMEFRAMES:
            return []
        return super().on_bar(timeframe_minutes, bar)

    @property
    def diagnostics(self) -> dict[str, Any]:
        output = dict(super().diagnostics)
        output["accepted_timeframes"] = tuple(sorted(self.SUPPORTED_TIMEFRAMES, reverse=True))
        output["timeframe_policy"] = LOCAL_ENGINE_TIMEFRAME_POLICY
        return output


class EasyChartRE1ML3BasePolicyBundle(EasyChartRE1CompleteBotPolicyV2Bundle):
    """Complete EasyChart opportunity set with executable local source contracts."""

    def __init__(self, symbol: str, tick_size: float, minimum_gross_rr: float = 1.0) -> None:
        super().__init__(symbol, tick_size, minimum_gross_rr)
        self.mature_diagonal_acceptance = ContractedMatureDiagonalResponseFamily(
            symbol,
            tick_size,
            minimum_gross_rr,
        )

    @property
    def diagnostics(self) -> dict[str, Any]:
        output = dict(super().diagnostics)
        output["local_engine_timeframe_contract"] = {
            "policy": LOCAL_ENGINE_TIMEFRAME_POLICY,
            "horizontal": (15, 5, 1),
            "mature_diagonal": (15, 5, 1),
        }
        return output


MultiScaleScenarioBundle = EasyChartRE1ML3BasePolicyBundle
StrategyClass = EasyChartRE1LocalAuctionStrategy


__all__ = [
    "ContractedMatureDiagonalResponseFamily",
    "EasyChartRE1ML3BasePolicyBundle",
    "LOCAL_ENGINE_TIMEFRAME_POLICY",
    "MultiScaleScenarioBundle",
    "StrategyClass",
]
