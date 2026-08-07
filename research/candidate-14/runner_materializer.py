"""Fail-closed materialization of Candidate 13's Nautilus parent-order branch.

The inherited portfolio runner has one production boundary that always builds a
passive GTD limit parent.  Candidate 13 FAR plans may explicitly request an
immediate MARKET parent after their after-cost R is recomputed at the completed
confirmation close.  NautilusTrader's Cython ``OrderFactory`` type is immutable,
so the branch is inserted into the local runner source before it is compiled.

The exact old block must occur once.  Any upstream drift raises before data is
loaded or a backtest starts.
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

NEW_ORDER_BLOCK = '''                # candidate-13-market-parent: execution remains inside NautilusTrader.
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
                        entry_post_only=True,
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
            "Candidate 13 order-boundary contract drifted: "
            f"expected one inherited bracket block, found {occurrences}",
        )
    materialized = source.replace(OLD_ORDER_BLOCK, NEW_ORDER_BLOCK, 1)
    if materialized.count("candidate-13-market-parent") != 1:
        raise RuntimeError("Candidate 13 market-parent branch was not materialized exactly once")
    return materialized
