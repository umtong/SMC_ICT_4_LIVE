"""One-minute lifecycle completion for active liquidity delivery.

The matching-scale draw is a state, not a permission that may linger until the
next five-minute close.  Once any completed one-minute bar trades the external
draw or the source invalidation, later local entries no longer belong to that
delivery episode.
"""
from __future__ import annotations

from domain import Candle
from easychart_re1_delivery_draw_v4_fixed import (
    FlowValidatedLiquidityDrawFixed,
)


class FlowValidatedLiquidityDrawV5(FlowValidatedLiquidityDrawFixed):
    def on_bar(self, timeframe_minutes: int, bar: Candle) -> None:
        if timeframe_minutes == self.TRIGGER_MINUTES:
            self._advance_active(bar)
        super().on_bar(timeframe_minutes, bar)
