"""Candidate 18 v5: trade-tick triggered local stop with native venue exit.

The signal and risk geometry are inherited unchanged from v4. The only change is
execution causality: every filled IOC tranche receives a locally emulated
STOP_MARKET keyed to real LAST_PRICE TradeTicks. The conditional order is held
inside NautilusTrader rather than waiting for venue insertion. Once an actual
trade reaches the stop, Nautilus releases a reduce-only MARKET order through the
normal risk, latency, matching, fee, position and NAV pipeline.
"""
from __future__ import annotations

from typing import Any

from nautilus_trader.model.enums import OrderSide
from nautilus_trader.model.enums import TimeInForce
from nautilus_trader.model.enums import TriggerType

from managed_protection_ioc_strategy import Candidate18Config
from managed_protection_ioc_strategy import Candidate18Strategy as _Candidate18V4Strategy
from managed_protection_ioc_strategy import _number


class Candidate18Strategy(_Candidate18V4Strategy):
    """Keep protection local until a real trade crosses structural invalidation."""

    def __init__(self, config: Candidate18Config) -> None:
        super().__init__(config=config)
        self.diagnostics.update(
            {
                "candidate18_v5_trade_tick_stop_batches": 0,
                "candidate18_v5_trade_tick_stop_qty": 0.0,
            },
        )

    def _submit_pending_protection(self, event: Any | None = None) -> None:
        quantity_value = self._pending_protection_qty
        if quantity_value <= 1e-12:
            return
        if self._managed_side == 0:
            self._fail_close(event, "MISSING_MANAGED_ENTRY_SIDE")
            return
        fill_price = (
            _number(getattr(event, "last_px", self.bars[-1]["close"]))
            if event is not None
            else float(self.bars[-1]["close"])
        )
        if self._stop_crossed(fill_price):
            self.diagnostics["candidate18_v4_crossed_stop_fail_closes"] = int(
                self.diagnostics["candidate18_v4_crossed_stop_fail_closes"],
            ) + 1
            self._fail_close(event, "ACTUAL_FILL_ALREADY_CROSSED_TRADE_TICK_STOP")
            return

        self._tranche_counter += 1
        exit_side = OrderSide.SELL if self._managed_side > 0 else OrderSide.BUY
        tags = [
            f"CANDIDATE18_MANAGED_TRANCHE_{self._tranche_counter}",
            str(self.current_scenario_id or "UNKNOWN"),
        ]
        stop_order = self.order_factory.stop_market(
            instrument_id=self.config.instrument_id,
            order_side=exit_side,
            quantity=self.instrument.make_qty(quantity_value),
            trigger_price=self.instrument.make_price(self._managed_stop),
            trigger_type=TriggerType.LAST_PRICE,
            time_in_force=TimeInForce.GTC,
            reduce_only=True,
            emulation_trigger=TriggerType.LAST_PRICE,
            tags=[
                "CANDIDATE18_MANAGED_STOP",
                "CANDIDATE18_TRADE_TICK_EMULATED_STOP",
                *tags,
            ],
        )
        target_order = self.order_factory.limit(
            instrument_id=self.config.instrument_id,
            order_side=exit_side,
            quantity=self.instrument.make_qty(quantity_value),
            price=self.instrument.make_price(self._managed_target),
            time_in_force=TimeInForce.GTC,
            post_only=False,
            reduce_only=True,
            tags=["CANDIDATE18_MANAGED_TARGET", *tags],
        )
        self._protective_ids.update(
            {str(stop_order.client_order_id), str(target_order.client_order_id)},
        )
        self.submit_order(stop_order)
        self.submit_order(target_order)
        self.diagnostics["candidate18_v4_protection_batches"] = int(
            self.diagnostics["candidate18_v4_protection_batches"],
        ) + 1
        self.diagnostics["candidate18_v4_stop_qty_submitted"] = float(
            self.diagnostics["candidate18_v4_stop_qty_submitted"],
        ) + quantity_value
        self.diagnostics["candidate18_v4_target_qty_submitted"] = float(
            self.diagnostics["candidate18_v4_target_qty_submitted"],
        ) + quantity_value
        self.diagnostics["candidate18_v5_trade_tick_stop_batches"] = int(
            self.diagnostics["candidate18_v5_trade_tick_stop_batches"],
        ) + 1
        self.diagnostics["candidate18_v5_trade_tick_stop_qty"] = float(
            self.diagnostics["candidate18_v5_trade_tick_stop_qty"],
        ) + quantity_value
        self._pending_protection_qty = 0.0


__all__ = ["Candidate18Config", "Candidate18Strategy"]
