"""Candidate 18 v7: keep both protective exits inside Nautilus until touched.

V6 proved that native TradeTick matching removes bar-fill optimism and that a
bounded, non-chasing GTD entry preserves the intended risk geometry. Its only
remaining execution failure was a cancellation race: after a locally emulated
stop flattened the position, a native take-profit LIMIT could reach the venue
late and be rejected as reduce-only.

This module keeps both mutually exclusive exits in NautilusTrader's order
emulator. A real LAST_PRICE TradeTick releases either a reduce-only STOP_MARKET
or a reduce-only MARKET_IF_TOUCHED as a native MARKET order. The untouched
sibling is still local, so cancel_all_orders closes it without venue latency.
"""
from __future__ import annotations

from typing import Any

from nautilus_trader.model.enums import OrderSide
from nautilus_trader.model.enums import TimeInForce
from nautilus_trader.model.enums import TriggerType

from bounded_gtd_entry_strategy import Candidate18Config
from bounded_gtd_entry_strategy import Candidate18Strategy as _Candidate18V6Strategy
from managed_protection_ioc_strategy import _number


class Candidate18Strategy(_Candidate18V6Strategy):
    """Use actual trade ticks for both structural stop and profit objective."""

    def __init__(self, config: Candidate18Config) -> None:
        super().__init__(config=config)
        self.diagnostics.update(
            {
                "candidate18_v7_local_twin_batches": 0,
                "candidate18_v7_local_stop_qty": 0.0,
                "candidate18_v7_local_target_qty": 0.0,
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
            self._fail_close(
                event,
                "ACTUAL_FILL_ALREADY_CROSSED_TRADE_TICK_STOP",
            )
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
            trigger_instrument_id=self.config.instrument_id,
            tags=[
                "CANDIDATE18_MANAGED_STOP",
                "CANDIDATE18_TRADE_TICK_EMULATED_STOP",
                "CANDIDATE18_V7_LOCAL_TWIN_STOP",
                *tags,
            ],
        )
        target_order = self.order_factory.market_if_touched(
            instrument_id=self.config.instrument_id,
            order_side=exit_side,
            quantity=self.instrument.make_qty(quantity_value),
            trigger_price=self.instrument.make_price(self._managed_target),
            trigger_type=TriggerType.LAST_PRICE,
            time_in_force=TimeInForce.GTC,
            reduce_only=True,
            emulation_trigger=TriggerType.LAST_PRICE,
            trigger_instrument_id=self.config.instrument_id,
            tags=[
                "CANDIDATE18_MANAGED_TARGET",
                "CANDIDATE18_TRADE_TICK_EMULATED_TARGET",
                "CANDIDATE18_V7_LOCAL_TWIN_TARGET",
                *tags,
            ],
        )

        stop_id = str(stop_order.client_order_id)
        target_id = str(target_order.client_order_id)
        self._protective_ids.update({stop_id, target_id})
        self._v5_stop_ids.add(stop_id)
        self._v5_target_ids.add(target_id)
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
        self.diagnostics["candidate18_v7_local_twin_batches"] = int(
            self.diagnostics["candidate18_v7_local_twin_batches"],
        ) + 1
        self.diagnostics["candidate18_v7_local_stop_qty"] = float(
            self.diagnostics["candidate18_v7_local_stop_qty"],
        ) + quantity_value
        self.diagnostics["candidate18_v7_local_target_qty"] = float(
            self.diagnostics["candidate18_v7_local_target_qty"],
        ) + quantity_value
        self._pending_protection_qty = 0.0


__all__ = ["Candidate18Config", "Candidate18Strategy"]
