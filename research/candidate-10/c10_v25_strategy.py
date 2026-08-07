"""NautilusTrader execution wrapper for candidate-10 v25."""
from __future__ import annotations

from typing import Any

from c10_liquidation_strategy import LiquidationCandidate10Config
from c10_live_cost_ledger import LiveCostLiquidationStrategy
from c10_v25_model import LiquidityResponseBar, LiquidityResponseParams
from c10_v25_strict_state import StrictLiquidityResponseStateMachine


LiquidityResponseCandidate10Config = LiquidationCandidate10Config


class LiquidityResponseCandidate10Strategy(LiveCostLiquidationStrategy):
    """Execute completed liquidity-response plans through NautilusTrader."""

    def on_start(self) -> None:
        self.instrument = self.cache.instrument(self.config.instrument_id)
        if self.instrument is None:
            raise RuntimeError(
                f"instrument not in cache: {self.config.instrument_id}",
            )
        params = LiquidityResponseParams(**dict(self.config.params))
        self.machine = StrictLiquidityResponseStateMachine(
            params,
            tick_size=self.instrument.price_increment.as_double(),
            instrument_id=str(self.config.instrument_id),
        )
        self.subscribe_trade_ticks(self.config.instrument_id)
        self.subscribe_bars(self.config.bar_type)

    @staticmethod
    def _row_to_bar(row: dict[str, Any]) -> LiquidityResponseBar:
        return LiquidityResponseBar(
            ts_ns=int(row["ts_ns"]),
            mid_open=float(row["mid_open"]),
            mid_high=float(row["mid_high"]),
            mid_low=float(row["mid_low"]),
            mid_close=float(row["mid_close"]),
            bid_price=float(row["bid_price"]),
            ask_price=float(row["ask_price"]),
            bid_size=float(row["bid_size"]),
            ask_size=float(row["ask_size"]),
            mean_spread=float(row["mean_spread"]),
            max_spread=float(row["max_spread"]),
            quote_updates=int(row["quote_updates"]),
            ofi_qty=float(row["ofi_qty"]),
            bid_add_qty=float(row["bid_add_qty"]),
            bid_remove_qty=float(row["bid_remove_qty"]),
            ask_add_qty=float(row["ask_add_qty"]),
            ask_remove_qty=float(row["ask_remove_qty"]),
            trade_quote_volume=float(row["trade_quote_volume"]),
            taker_buy_quote=float(row["taker_buy_quote"]),
            trade_base_volume=float(row["trade_base_volume"]),
            taker_buy_base=float(row["taker_buy_base"]),
            trade_count=int(row["trade_count"]),
        )

    def _submit_entry(self, plan: Any, tick: Any) -> bool:
        submitted = super()._submit_entry(plan, tick)
        if submitted and self.active_trade is not None:
            self.active_trade["entry_order_type"] = (
                "MARKET_ON_FIRST_POST_CONFIRMATION_AGGTRADE"
            )
            self.active_trade["execution_family"] = (
                "LIQUIDITY_SHELF_TOP_OF_BOOK_RESPONSE"
            )
        return submitted


__all__ = [
    "LiquidityResponseCandidate10Config",
    "LiquidityResponseCandidate10Strategy",
]
