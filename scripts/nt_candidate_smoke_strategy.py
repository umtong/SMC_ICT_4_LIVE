"""Minimal strategy used only to verify the candidate's NautilusTrader path."""
from __future__ import annotations

from decimal import Decimal

from nautilus_trader.config import StrategyConfig
from nautilus_trader.model.data import QuoteTick
from nautilus_trader.model.enums import OrderSide
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.trading.strategy import Strategy


class CandidateNTSmokeConfig(StrategyConfig, frozen=True):
    instrument_id: InstrumentId
    trade_size: Decimal = Decimal("0.01")


class CandidateNTSmoke(Strategy):
    def __init__(self, config: CandidateNTSmokeConfig) -> None:
        super().__init__(config=config)
        self.instrument = None
        self.quotes_seen = 0
        self.open_submitted = False
        self.close_submitted = False

    def on_start(self) -> None:
        self.instrument = self.cache.instrument(self.config.instrument_id)
        if self.instrument is None:
            raise RuntimeError(f"instrument not in cache: {self.config.instrument_id}")
        self.subscribe_quote_ticks(self.config.instrument_id)

    def on_quote_tick(self, tick: QuoteTick) -> None:
        self.quotes_seen += 1
        if not self.open_submitted:
            order = self.order_factory.market(
                instrument_id=self.config.instrument_id,
                order_side=OrderSide.BUY,
                quantity=self.instrument.make_qty(self.config.trade_size),
            )
            self.submit_order(order)
            self.open_submitted = True
            return
        if (
            self.quotes_seen >= 5
            and not self.close_submitted
            and not self.portfolio.is_flat(self.config.instrument_id)
        ):
            self.close_all_positions(self.config.instrument_id, reduce_only=True)
            self.close_submitted = True

    def on_stop(self) -> None:
        self.cancel_all_orders(self.config.instrument_id)
        if not self.portfolio.is_flat(self.config.instrument_id):
            self.close_all_positions(self.config.instrument_id, reduce_only=True)
