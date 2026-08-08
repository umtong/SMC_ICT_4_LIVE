"""Minimal structural-MSS correction for the candidate-07 router.

The base router records ``OPPOSITE_DISPLACEMENT_MSS`` after an opposite-colour
body merely closes back through the swept *external* level.  The episode already
stores a distinct, pre-sweep internal boundary in ``trigger_price`` but the base
confirmation never uses it.  This module changes that one logical statement:
confirmation requires a completed opposite body to close through the stored
internal boundary.

No source-liquidity rule, confirmation horizon, body threshold, entry, stop,
target, management, fee, risk or execution rule is changed.  No independent
retest or additional fitted threshold is introduced.
"""
from __future__ import annotations

from typing import Any

from model import CausalLiquidityRouter, Direction, SignalBar


class InternalBoundaryMSSRouter(CausalLiquidityRouter):
    """Confirm reversal only after the pre-sweep internal range is displaced."""

    def _reversal_confirmed(
        self,
        episode: Any,
        bar: SignalBar,
        atr: float,
    ) -> bool:
        body_ok = bar.body >= self.config.reverse_confirm_body_atr * atr
        trigger = float(episode.trigger_price)
        if episode.direction is Direction.SHORT:
            return body_ok and bar.close < trigger and bar.close < bar.open
        return body_ok and bar.close > trigger and bar.close > bar.open


__all__ = ["InternalBoundaryMSSRouter"]
