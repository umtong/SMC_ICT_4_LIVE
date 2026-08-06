#!/usr/bin/env python3
"""NautilusTrader 1.230.0 object-level contract for the ADOM bracket."""

from __future__ import annotations

from datetime import datetime, timezone

from nautilus_trader.common.component import TestClock
from nautilus_trader.common.factories import OrderFactory
from nautilus_trader.core.datetime import dt_to_unix_nanos
from nautilus_trader.model.enums import (
    ContingencyType,
    OrderSide,
    OrderType,
    TimeInForce,
)
from nautilus_trader.model.identifiers import InstrumentId, StrategyId, TraderId
from nautilus_trader.model.objects import Price, Quantity


def main() -> int:
    factory = OrderFactory(
        trader_id=TraderId("TRADER-001"),
        strategy_id=StrategyId("ADOM-001"),
        clock=TestClock(),
    )
    expiry = datetime(2024, 2, 26, 16, 30, tzinfo=timezone.utc)
    order_list = factory.bracket(
        instrument_id=InstrumentId.from_str("BTCUSDT-PERP.BINANCE"),
        order_side=OrderSide.BUY,
        quantity=Quantity.from_str("0.100"),
        entry_order_type=OrderType.LIMIT,
        entry_price=Price.from_str("60000.0"),
        time_in_force=TimeInForce.GTD,
        expire_time=expiry,
        entry_post_only=True,
        tp_price=Price.from_str("60600.0"),
        tp_time_in_force=TimeInForce.GTC,
        tp_post_only=True,
        sl_trigger_price=Price.from_str("59700.0"),
        sl_time_in_force=TimeInForce.GTC,
    )

    entry, stop, target = order_list.orders
    assert entry.order_type == OrderType.LIMIT
    assert entry.time_in_force == TimeInForce.GTD
    assert entry.expire_time_ns == dt_to_unix_nanos(expiry)
    assert entry.is_post_only
    assert not entry.is_reduce_only
    assert entry.contingency_type == ContingencyType.OTO
    assert set(entry.linked_order_ids) == {
        stop.client_order_id,
        target.client_order_id,
    }

    assert stop.order_type == OrderType.STOP_MARKET
    assert stop.time_in_force == TimeInForce.GTC
    assert stop.expire_time_ns == 0
    assert stop.is_reduce_only
    assert stop.contingency_type == ContingencyType.OUO
    assert stop.parent_order_id == entry.client_order_id
    assert stop.linked_order_ids == [target.client_order_id]

    assert target.order_type == OrderType.LIMIT
    assert target.time_in_force == TimeInForce.GTC
    assert target.expire_time_ns == 0
    assert target.is_post_only
    assert target.is_reduce_only
    assert target.contingency_type == ContingencyType.OUO
    assert target.parent_order_id == entry.client_order_id
    assert target.linked_order_ids == [stop.client_order_id]

    print("ADOM native Nautilus bracket contract passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
