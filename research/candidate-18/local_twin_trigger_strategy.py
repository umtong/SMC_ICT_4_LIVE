"""Candidate 18 v7: keep both protective exits inside Nautilus until touched.

V6 proved that native TradeTick matching removes bar-fill optimism and that a
bounded, non-chasing GTD entry preserves the intended risk geometry. Its only
remaining execution failure was a cancellation race: after a locally emulated
stop flattened the position, a native take-profit LIMIT could reach the venue
late and be rejected as reduce-only.

This module keeps both mutually exclusive exits in NautilusTrader's order
emulator. A real LAST_PRICE TradeTick releases either a reduce-only STOP_MARKET
or a reduce-only MARKET_IF_TOUCHED as a native MARKET order. The first local
release cancels the opposite family before it can leave the emulator. Multiple
same-side tranche exits then complete as one exit wave without re-arming a new
protective order against fills which are already in flight.
"""
from __future__ import annotations

from typing import Any

from nautilus_trader.model.enums import OrderSide
from nautilus_trader.model.enums import TimeInForce
from nautilus_trader.model.enums import TriggerType
from nautilus_trader.model.identifiers import ClientOrderId

from bounded_gtd_entry_strategy import Candidate18Config
from bounded_gtd_entry_strategy import Candidate18Strategy as _Candidate18V6Strategy
from managed_protection_ioc_strategy import _number


class Candidate18Strategy(_Candidate18V6Strategy):
    """Use actual trade ticks for both structural stop and profit objective."""

    def __init__(self, config: Candidate18Config) -> None:
        super().__init__(config=config)
        self._v7_exit_wave: str | None = None
        self.diagnostics.update(
            {
                "candidate18_v7_local_twin_batches": 0,
                "candidate18_v7_local_stop_qty": 0.0,
                "candidate18_v7_local_target_qty": 0.0,
                "candidate18_v7_stop_waves": 0,
                "candidate18_v7_target_waves": 0,
                "candidate18_v7_opposite_release_events": 0,
            },
        )

    def _clear_trade_state(self) -> None:
        super()._clear_trade_state()
        if hasattr(self, "_v7_exit_wave"):
            self._v7_exit_wave = None

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

    def _cancel_local_family(self, identifiers: set[str]) -> None:
        for identifier in tuple(identifiers):
            self.cancel_order(ClientOrderId.from_str(identifier))

    def on_order_released(self, event: Any) -> None:
        client_order_id = str(getattr(event, "client_order_id", ""))
        if client_order_id in self._v5_target_ids:
            if self._v7_exit_wave is None:
                self._v7_exit_wave = "TARGET"
                self.diagnostics["candidate18_v7_target_waves"] = int(
                    self.diagnostics["candidate18_v7_target_waves"],
                ) + 1
                self._cancel_local_family(self._v5_stop_ids)
            elif self._v7_exit_wave != "TARGET":
                self.diagnostics["candidate18_v7_opposite_release_events"] = int(
                    self.diagnostics["candidate18_v7_opposite_release_events"],
                ) + 1
        elif client_order_id in self._v5_stop_ids:
            if self._v7_exit_wave is None:
                self._v7_exit_wave = "STOP"
                self.diagnostics["candidate18_v7_stop_waves"] = int(
                    self.diagnostics["candidate18_v7_stop_waves"],
                ) + 1
                self._cancel_local_family(self._v5_target_ids)
            elif self._v7_exit_wave != "STOP":
                self.diagnostics["candidate18_v7_opposite_release_events"] = int(
                    self.diagnostics["candidate18_v7_opposite_release_events"],
                ) + 1
        super().on_order_released(event)

    def on_order_filled(self, event: Any) -> None:
        client_order_id = str(getattr(event, "client_order_id", ""))
        if client_order_id not in self._v5_target_ids:
            super().on_order_filled(event)
            return

        # All target tranches share one trigger and release as one wave. Do not
        # re-arm protection after the first target fill because the remaining
        # original MARKET exits are already in flight. Re-arming here created
        # the v7 duplicate reduce-only rejection discovered in the native log.
        fill_qty = _number(getattr(event, "last_qty", 0.0))
        self._managed_open_qty = max(0.0, self._managed_open_qty - fill_qty)
        self.diagnostics["candidate18_v4_exit_fill_events"] = int(
            self.diagnostics["candidate18_v4_exit_fill_events"],
        ) + 1
        self.diagnostics["candidate18_v5_target_fill_events"] = int(
            self.diagnostics["candidate18_v5_target_fill_events"],
        ) + 1
        if self._managed_open_qty <= 1e-12:
            self.cancel_all_orders(self.config.instrument_id)
            self._protective_ids.clear()
            self._v5_stop_ids.clear()
            self._v5_target_ids.clear()
        return


__all__ = ["Candidate18Config", "Candidate18Strategy"]
