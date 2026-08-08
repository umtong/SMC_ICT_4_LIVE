"""Fail-closed materialization of Candidate 13 V5 parent-order semantics.

The inherited portfolio runner always builds a passive GTD parent. Session I7
plans can explicitly request either an immediate MARKET parent or a GTD LIMIT.
NautilusTrader remains the sole owner of order creation, fills, fees, margin,
positions and NAV. The exact inherited block must occur once or execution stops
before market data is loaded.
"""
from __future__ import annotations


OLD_ORDER_BLOCK = '''                order_list = self.order_factory.bracket(
                    instrument_id=instrument.id,
                    order_side=side,
                    quantity=instrument.make_qty(decision.quantity),
                    entry_order_type=OrderType.LIMIT,
                    entry_price=instrument.make_price(plan.expected_entry),
                    expire_time=datetime.fromtimestamp(plan.expire_ts_ns / 1_000_000_000, tz=UTC) + timedelta(microseconds=1),
                    time_in_force=TimeInForce.GTD,
                    entry_post_only=True,
                    tp_order_type=OrderType.LIMIT,
                    tp_price=instrument.make_price(plan.target_price),
                    tp_time_in_force=TimeInForce.GTC,
                    tp_post_only=True,
                    sl_order_type=OrderType.STOP_MARKET,
                    sl_trigger_price=instrument.make_price(plan.stop_price),
                    sl_time_in_force=TimeInForce.GTC,
                )'''

NEW_ORDER_BLOCK = '''                # candidate-13-v5-session-parent: NautilusTrader owns execution.
                if plan.entry_order_type == "MARKET":
                    order_list = self.order_factory.bracket(
                        instrument_id=instrument.id,
                        order_side=side,
                        quantity=instrument.make_qty(decision.quantity),
                        entry_order_type=OrderType.MARKET,
                        time_in_force=TimeInForce.GTC,
                        tp_order_type=OrderType.LIMIT,
                        tp_price=instrument.make_price(plan.target_price),
                        tp_time_in_force=TimeInForce.GTC,
                        tp_post_only=True,
                        sl_order_type=OrderType.STOP_MARKET,
                        sl_trigger_price=instrument.make_price(plan.stop_price),
                        sl_time_in_force=TimeInForce.GTC,
                    )
                else:
                    order_list = self.order_factory.bracket(
                        instrument_id=instrument.id,
                        order_side=side,
                        quantity=instrument.make_qty(decision.quantity),
                        entry_order_type=OrderType.LIMIT,
                        entry_price=instrument.make_price(plan.expected_entry),
                        expire_time=datetime.fromtimestamp(plan.expire_ts_ns / 1_000_000_000, tz=UTC) + timedelta(microseconds=1),
                        time_in_force=TimeInForce.GTD,
                        entry_post_only=bool(plan.entry_post_only),
                        tp_order_type=OrderType.LIMIT,
                        tp_price=instrument.make_price(plan.target_price),
                        tp_time_in_force=TimeInForce.GTC,
                        tp_post_only=True,
                        sl_order_type=OrderType.STOP_MARKET,
                        sl_trigger_price=instrument.make_price(plan.stop_price),
                        sl_time_in_force=TimeInForce.GTC,
                    )'''


def materialize_runner_source(source: str) -> str:
    occurrences = source.count(OLD_ORDER_BLOCK)
    if occurrences != 1:
        raise RuntimeError(
            "Candidate 13 V5 order boundary drifted: "
            f"expected one inherited bracket block, found {occurrences}",
        )
    materialized = source.replace(OLD_ORDER_BLOCK, NEW_ORDER_BLOCK, 1)
    if materialized.count("candidate-13-v5-session-parent") != 1:
        raise RuntimeError("Candidate 13 V5 parent branch was not materialized exactly once")
    return materialized
