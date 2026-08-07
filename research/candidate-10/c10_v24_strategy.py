"""NautilusTrader execution wrapper for candidate-10 v24.

Only completed cross-market rows drive the state machine. A confirmed plan is
submitted on the first strictly later raw perpetual aggregate TradeTick. The
existing Nautilus-owned position lifecycle and fill-time all-cost ledger remain
unchanged.
"""
from __future__ import annotations

from typing import Any

from c10_liquidation_strategy import LiquidationCandidate10Config
from c10_live_cost_ledger import LiveCostLiquidationStrategy
from c10_v24_model import CrossMarketBar, CrossMarketParams
from c10_v24_state import CrossMarketReconciliationStateMachine


CrossMarketCandidate10Config = LiquidationCandidate10Config


class CrossMarketCandidate10Strategy(LiveCostLiquidationStrategy):
    """Execute cross-market plans while Nautilus owns fills, positions and NAV."""

    def on_start(self) -> None:
        self.instrument = self.cache.instrument(self.config.instrument_id)
        if self.instrument is None:
            raise RuntimeError(
                f"instrument not in cache: {self.config.instrument_id}",
            )
        params = CrossMarketParams(**dict(self.config.params))
        self.machine = CrossMarketReconciliationStateMachine(
            params,
            tick_size=self.instrument.price_increment.as_double(),
            instrument_id=str(self.config.instrument_id),
        )
        self.subscribe_trade_ticks(self.config.instrument_id)
        self.subscribe_bars(self.config.bar_type)

    @staticmethod
    def _row_to_bar(row: dict[str, Any]) -> CrossMarketBar:
        return CrossMarketBar(
            ts_ns=int(row["ts_ns"]),
            spot_open=float(row["spot_open"]),
            spot_high=float(row["spot_high"]),
            spot_low=float(row["spot_low"]),
            spot_close=float(row["spot_close"]),
            spot_quote_volume=float(row["spot_quote_volume"]),
            spot_taker_buy_quote=float(row["spot_taker_buy_quote"]),
            spot_trade_count=int(row["spot_trade_count"]),
            perp_open=float(row["perp_open"]),
            perp_high=float(row["perp_high"]),
            perp_low=float(row["perp_low"]),
            perp_close=float(row["perp_close"]),
            perp_quote_volume=float(row["perp_quote_volume"]),
            perp_taker_buy_quote=float(row["perp_taker_buy_quote"]),
            perp_trade_count=int(row["perp_trade_count"]),
        )

    def _submit_entry(self, plan: Any, tick: Any) -> bool:
        submitted = super()._submit_entry(plan, tick)
        if submitted and self.active_trade is not None:
            self.active_trade["entry_order_type"] = (
                "MARKET_ON_FIRST_POST_RECONCILIATION_PERP_TRADE"
            )
            self.active_trade["execution_family"] = (
                "SPOT_PERPETUAL_AUCTION_RECONCILIATION"
            )
        return submitted


__all__ = [
    "CrossMarketCandidate10Config",
    "CrossMarketCandidate10Strategy",
]
