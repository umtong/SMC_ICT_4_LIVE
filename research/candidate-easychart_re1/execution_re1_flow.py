"""RE1 execution strategy which preserves Binance aggressor-flow bar fields."""
from __future__ import annotations

from typing import Any

from easychart_re1_flow import FlowCandle
from execution_re1 import EasyChartRE1Strategy


class EasyChartRE1FlowStrategy(EasyChartRE1Strategy):
    """Use the unchanged account/order lifecycle with extended one-minute bars."""

    @staticmethod
    def _candle(bar: Any) -> FlowCandle:
        if isinstance(bar, FlowCandle):
            return bar
        return FlowCandle(
            ts_close_ns=int(bar.ts_event),
            open=float(bar.open),
            high=float(bar.high),
            low=float(bar.low),
            close=float(bar.close),
            volume=float(bar.volume),
            quote_volume=float(getattr(bar, "quote_volume", 0.0)),
            trade_count=int(getattr(bar, "count", 0)),
            taker_buy_base_volume=float(
                getattr(bar, "taker_buy_base_volume", 0.0),
            ),
            taker_buy_quote_volume=float(
                getattr(bar, "taker_buy_quote_volume", 0.0),
            ),
        )
