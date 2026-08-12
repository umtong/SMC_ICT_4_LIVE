"""Deterministic execution smoke for half-profit then breakeven protection.

The source's core scalp management is operationally non-trivial: take half at
the first objective, then move the remaining stop to average entry. This test
uses a native Nautilus bracket for atomic initial protection, reduces the linked
take-profit child to half after the real entry fill, and installs a new
breakeven stop for the remaining live position after that half target fills.

The script is a contract test, not a strategy backtest. It fails closed on an
unexpected quantity, missing protection, position flip, or non-flat finish.
"""
from __future__ import annotations

from decimal import Decimal, ROUND_DOWN
import json
from typing import Any

from nautilus_trader.model.data import Bar, BarType
from nautilus_trader.model.enums import OrderSide, OrderType, TimeInForce, TriggerType
from nautilus_trader.model.events import OrderCanceled, OrderFilled, OrderUpdated, PositionClosed
from nautilus_trader.model.identifiers import ClientOrderId, Venue
from nautilus_trader.model.orders.list import OrderList
from nautilus_trader.trading.strategy import Strategy

from backtest_support import make_engine
from instruments import make_instrument


BASE_TS_NS = 1_704_067_200_000_000_000
ENTRY_PRICE = Decimal("100.0")
INITIAL_STOP = Decimal("95.0")
FIRST_TARGET = Decimal("105.0")
FULL_QTY = Decimal("1.000")


def split_half(quantity: Decimal, increment: Decimal) -> tuple[Decimal, Decimal]:
    if quantity <= 0 or increment <= 0:
        raise ValueError("quantity and increment must be positive")
    first = ((quantity / Decimal("2")) / increment).to_integral_value(
        rounding=ROUND_DOWN,
    ) * increment
    remainder = quantity - first
    if first <= 0 or remainder <= 0:
        raise ValueError("quantity cannot be split into two positive legs")
    return first, remainder


class HalfThenBreakevenSmoke(Strategy):
    def __init__(self, instrument_id, bar_type) -> None:  # type: ignore[no-untyped-def]
        super().__init__()
        self.instrument_id = instrument_id
        self.bar_type = bar_type
        self.entry_id: ClientOrderId | None = None
        self.initial_stop_id: ClientOrderId | None = None
        self.first_target_id: ClientOrderId | None = None
        self.breakeven_stop_id: ClientOrderId | None = None
        self.first_target_order = None
        self.entry_fill_price: Decimal | None = None
        self.first_leg_qty: Decimal | None = None
        self.remainder_qty: Decimal | None = None
        self.events: list[dict[str, Any]] = []
        self.bars_seen = 0
        self.position_closed_count = 0

    def _record(self, kind: str, **values: Any) -> None:
        self.events.append({"kind": kind, **values})

    def _open_positions(self):  # type: ignore[no-untyped-def]
        return self.cache.positions_open(
            instrument_id=self.instrument_id,
            strategy_id=self.id,
        )

    def on_start(self) -> None:
        self.subscribe_bars(self.bar_type)

    def on_bar(self, bar: Bar) -> None:
        if self.bars_seen == 0:
            instrument = self.cache.instrument(self.instrument_id)
            if instrument is None:
                raise RuntimeError("instrument unavailable")
            quantity = instrument.make_qty(FULL_QTY)
            order_list: OrderList = self.order_factory.bracket(
                instrument_id=self.instrument_id,
                order_side=OrderSide.BUY,
                quantity=quantity,
                time_in_force=TimeInForce.GTC,
                sl_trigger_price=instrument.make_price(INITIAL_STOP),
                tp_price=instrument.make_price(FIRST_TARGET),
                entry_order_type=OrderType.MARKET,
                entry_post_only=False,
                tp_post_only=False,
                emulation_trigger=TriggerType.NO_TRIGGER,
                entry_tags=["ROLE:ENTRY"],
                sl_tags=["ROLE:INITIAL_STOP"],
                tp_tags=["ROLE:FIRST_HALF_TARGET"],
            )
            self.entry_id = order_list.orders[0].client_order_id
            self.initial_stop_id = order_list.orders[1].client_order_id
            self.first_target_id = order_list.orders[2].client_order_id
            self.first_target_order = order_list.orders[2]
            self.submit_order_list(order_list)
            self._record(
                "bracket_submitted",
                entry_id=str(self.entry_id),
                stop_id=str(self.initial_stop_id),
                target_id=str(self.first_target_id),
            )
        self.bars_seen += 1

    def on_order_updated(self, event: OrderUpdated) -> None:
        self._record(
            "order_updated",
            client_order_id=str(event.client_order_id),
            quantity=str(event.quantity),
            trigger_price=None if event.trigger_price is None else str(event.trigger_price),
        )

    def on_order_canceled(self, event: OrderCanceled) -> None:
        self._record("order_canceled", client_order_id=str(event.client_order_id))

    def on_order_filled(self, event: OrderFilled) -> None:
        self._record(
            "order_filled",
            client_order_id=str(event.client_order_id),
            last_qty=str(event.last_qty),
            last_px=str(event.last_px),
            position_id=None if event.position_id is None else str(event.position_id),
        )
        instrument = self.cache.instrument(self.instrument_id)
        if instrument is None:
            raise RuntimeError("instrument unavailable on fill")

        if event.client_order_id == self.entry_id:
            if self.entry_fill_price is not None:
                raise RuntimeError("entry filled more than once")
            self.entry_fill_price = event.last_px.as_decimal()
            first, remainder = split_half(
                event.last_qty.as_decimal(),
                instrument.size_increment.as_decimal(),
            )
            self.first_leg_qty = first
            self.remainder_qty = remainder
            if self.first_target_order is None:
                raise RuntimeError("first target order missing")
            self.modify_order(
                self.first_target_order,
                quantity=instrument.make_qty(first),
            )
            self._record(
                "first_target_resize_requested",
                first_leg_qty=str(first),
                remainder_qty=str(remainder),
                entry_fill_price=str(self.entry_fill_price),
            )
            return

        if event.client_order_id == self.first_target_id:
            if self.entry_fill_price is None or self.remainder_qty is None:
                raise RuntimeError("half target filled before entry state")
            positions = self._open_positions()
            if len(positions) != 1:
                raise RuntimeError(f"expected one remaining position, found {len(positions)}")
            live_qty = positions[0].quantity.as_decimal()
            if live_qty != self.remainder_qty:
                raise RuntimeError(
                    f"remaining quantity mismatch: {live_qty} != {self.remainder_qty}",
                )
            order = self.order_factory.stop_market(
                instrument_id=self.instrument_id,
                order_side=OrderSide.SELL,
                quantity=instrument.make_qty(live_qty),
                trigger_price=instrument.make_price(self.entry_fill_price),
                trigger_type=TriggerType.LAST_PRICE,
                time_in_force=TimeInForce.GTC,
                reduce_only=True,
                tags=["ROLE:BREAKEVEN_STOP"],
            )
            self.breakeven_stop_id = order.client_order_id
            self.submit_order(order, position_id=positions[0].id)
            self._record(
                "breakeven_stop_submitted",
                client_order_id=str(order.client_order_id),
                quantity=str(live_qty),
                trigger_price=str(self.entry_fill_price),
            )
            return

        if event.client_order_id == self.breakeven_stop_id:
            positions = self._open_positions()
            if positions:
                raise RuntimeError("breakeven stop filled but position remains open")

    def on_position_closed(self, event: PositionClosed) -> None:
        self.position_closed_count += 1
        self._record(
            "position_closed",
            realized_pnl=None if event.realized_pnl is None else str(event.realized_pnl),
            quantity=str(event.quantity),
        )

    def on_stop(self) -> None:
        self.cancel_all_orders(self.instrument_id)
        if not self.portfolio.is_flat(self.instrument_id):
            self.close_all_positions(self.instrument_id)
        self.unsubscribe_bars(self.bar_type)


def _bars(instrument, bar_type):  # type: ignore[no-untyped-def]
    rows = (
        # Submit bracket; market entry will execute on the next bar.
        (100.0, 100.2, 99.8, 100.0),
        # Entry fill at 100; neither stop nor target trades.
        (100.0, 102.0, 99.5, 101.0),
        # First target trades after the resize to half.
        (101.0, 106.0, 100.5, 105.5),
        # Remainder returns to average entry and exits at breakeven.
        (105.5, 106.0, 99.5, 100.0),
        (100.0, 101.0, 99.5, 100.0),
    )
    output = []
    for index, (open_, high, low, close) in enumerate(rows, start=1):
        timestamp = BASE_TS_NS + index * 60_000_000_000
        output.append(
            Bar(
                bar_type=bar_type,
                open=instrument.make_price(open_),
                high=instrument.make_price(high),
                low=instrument.make_price(low),
                close=instrument.make_price(close),
                volume=instrument.make_qty(Decimal("1000")),
                ts_event=timestamp,
                ts_init=timestamp,
            ),
        )
    return output


def verify_half_then_breakeven() -> dict[str, Any]:
    engine = make_engine()
    instrument = make_instrument("BTCUSDT")
    bar_type = BarType.from_str(f"{instrument.id}-1-MINUTE-LAST-EXTERNAL")
    engine.add_instrument(instrument)
    engine.add_data(_bars(instrument, bar_type))
    strategy = HalfThenBreakevenSmoke(instrument.id, bar_type)
    engine.add_strategy(strategy)
    try:
        engine.run()
        if not engine.portfolio.is_flat(instrument.id):
            raise RuntimeError("smoke account did not finish flat")
        if strategy.position_closed_count != 1:
            raise RuntimeError(
                f"expected one closed position, got {strategy.position_closed_count}",
            )
        fills = engine.trader.generate_order_fills_report()
        orders = engine.trader.generate_orders_report()
        positions = engine.trader.generate_positions_report()
        if len(fills.index) != 3:
            raise RuntimeError(f"expected entry, half target, BE stop fills; got {len(fills.index)}")
        if positions.empty:
            raise RuntimeError("position report is empty")
        filled_quantities = sorted(Decimal(str(value)) for value in fills["last_qty"].tolist())
        if filled_quantities != [Decimal("0.500"), Decimal("0.500"), Decimal("1.000")]:
            raise RuntimeError(f"unexpected fill quantities: {filled_quantities}")
        return {
            "fills": int(len(fills.index)),
            "orders": int(len(orders.index)),
            "closed_positions": int(len(positions.index)),
            "filled_quantities": [str(value) for value in filled_quantities],
            "events": strategy.events,
            "realized_pnl": str(positions.iloc[-1].get("realized_pnl")),
        }
    finally:
        engine.dispose()


if __name__ == "__main__":
    print(json.dumps(verify_half_then_breakeven(), indent=2, sort_keys=True))
