#!/usr/bin/env python3
"""Causally maintain swing-pool consumption on every completed bar."""
from __future__ import annotations

import math
from typing import Any

from nt_liquidity_strategy import LiquidityTransitionConfig
from nt_swing_pool_strategy import SwingPoolFailedAuctionStrategy


class _MaintainedSwingPoolStrategy(SwingPoolFailedAuctionStrategy):
    """Keep pool validity correct even when trading is disabled or occupied."""

    def _try_confirm_pending(self, row: dict[str, float | int]) -> bool:
        had_pending = self.pending is not None
        handled = super()._try_confirm_pending(row)
        return handled or (had_pending and self.pending is None)

    def on_bar(self, bar: Any) -> None:
        super().on_bar(bar)
        if not self.bars:
            return
        row = self.bars[-1]
        atr = self._atr()
        if not math.isfinite(atr) or atr <= 0.0:
            return
        for pool in self.swing_pools:
            if not pool.active:
                continue
            if self.bar_index - pool.observed_index > self.POOL_MAX_AGE_BARS:
                pool.active = False
                continue
            penetration = (
                (float(row["high"]) - pool.level) / atr
                if pool.side > 0
                else (pool.level - float(row["low"])) / atr
            )
            if penetration >= self.SWEEP_MIN_ATR:
                # A pool crossed while outside the entry gate, during an open
                # position, or on its observation bar is no longer future liquidity.
                pool.active = False


class SwingPoolReversal12MaintainedStrategy(_MaintainedSwingPoolStrategy):
    TARGET_NET_R = 1.20


class SwingPoolReversal16MaintainedStrategy(_MaintainedSwingPoolStrategy):
    TARGET_NET_R = 1.60


__all__ = [
    "LiquidityTransitionConfig",
    "SwingPoolReversal12MaintainedStrategy",
    "SwingPoolReversal16MaintainedStrategy",
]
