#!/usr/bin/env python3
"""Guarded target variants for the causal swing-pool strategy."""
from __future__ import annotations

from nt_liquidity_strategy import LiquidityTransitionConfig
from nt_swing_pool_strategy import SwingPoolFailedAuctionStrategy


class _GuardedSwingPoolStrategy(SwingPoolFailedAuctionStrategy):
    """Treat a consumed pending state as handled before base expiry logic runs."""

    def _try_confirm_pending(self, row: dict[str, float | int]) -> bool:
        had_pending = self.pending is not None
        handled = super()._try_confirm_pending(row)
        consumed = had_pending and self.pending is None
        return handled or consumed


class SwingPoolReversal12Strategy(_GuardedSwingPoolStrategy):
    TARGET_NET_R = 1.20


class SwingPoolReversal16Strategy(_GuardedSwingPoolStrategy):
    TARGET_NET_R = 1.60


__all__ = [
    "LiquidityTransitionConfig",
    "SwingPoolReversal12Strategy",
    "SwingPoolReversal16Strategy",
]
