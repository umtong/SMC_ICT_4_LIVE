#!/usr/bin/env python3
"""Development variants for acceptance impact and latency.

These variants test one market-structure question only: after a statistically
extreme rejection has failed, is the prior V18c assumption that only low-impact
acceptance is tradable correct?

The detection sequence, pre-existing liquidity target hierarchy, invalidation,
NautilusTrader execution and 3% NAV risk contract are unchanged. Variants alter
only whether an already-confirmed rejection failure is admitted immediately:

* fast: acceptance must occur within three completed bars, but participation and
  displacement are not capped;
* all: any qualified acceptance before the causal 30-bar probe expires.

They are development controls, not final strategies.
"""
from __future__ import annotations

import math

from nt_composite_liquidity_strategy import AuctionScale
from nt_composite_liquidity_strategy import CompositeLiquidityRouterStrategy
from nt_low_impact_hybrid_target_strategy import LowImpactHybridTargetStrategy


class ExternalFastAcceptanceStrategy(LowImpactHybridTargetStrategy):
    DIRECT_MAX_DELAY_BARS = 3
    DIRECT_MAX_VOLUME_RATIO = math.inf
    DIRECT_MAX_BODY_ATR = math.inf


class ExternalAllAcceptanceStrategy(LowImpactHybridTargetStrategy):
    DIRECT_MAX_DELAY_BARS = 30
    DIRECT_MAX_VOLUME_RATIO = math.inf
    DIRECT_MAX_BODY_ATR = math.inf


class CompositeFastAcceptanceStrategy(CompositeLiquidityRouterStrategy):
    def _configure_scale(self, scale: AuctionScale) -> None:
        super()._configure_scale(scale)
        self.DIRECT_MAX_DELAY_BARS = 3
        self.DIRECT_MAX_VOLUME_RATIO = math.inf
        self.DIRECT_MAX_BODY_ATR = math.inf


class CompositeAllAcceptanceStrategy(CompositeLiquidityRouterStrategy):
    def _configure_scale(self, scale: AuctionScale) -> None:
        super()._configure_scale(scale)
        self.DIRECT_MAX_DELAY_BARS = 30
        self.DIRECT_MAX_VOLUME_RATIO = math.inf
        self.DIRECT_MAX_BODY_ATR = math.inf


__all__ = [
    "CompositeAllAcceptanceStrategy",
    "CompositeFastAcceptanceStrategy",
    "ExternalAllAcceptanceStrategy",
    "ExternalFastAcceptanceStrategy",
]
