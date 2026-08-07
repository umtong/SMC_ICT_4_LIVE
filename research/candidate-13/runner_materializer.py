"""Fail-closed materialization of Candidate 13's Nautilus boundaries.

The inherited portfolio runner always builds a passive GTD parent and has no
post-leadership execution hook.  Candidate 13 inserts exactly two local
branches before compiling the runner:

* a plan explicitly marked MARKET builds a Nautilus MARKET bracket;
* after synchronized semantic approval, a pre-priced execution amendment may
  replace the immutable TradePlan.

Both inherited source blocks must occur exactly once.  Upstream drift raises
before data is loaded or a backtest starts.
"""
from __future__ import annotations


OLD_ORDER_BLOCK = """                order_list = self.order_factory.bracket(
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
                )"""

NEW_ORDER_BLOCK = """                # candidate-13-market-parent: execution remains inside NautilusTrader.
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
                    )"""

OLD_POST_GATE_BLOCK = """                plan.details["market_leadership"] = leadership.to_dict()
                if not leadership.approved:"""

NEW_POST_GATE_BLOCK = """                plan.details["market_leadership"] = leadership.to_dict()
                # candidate-13-post-gate: immutable plan amendment after synchronized semantics.
                plan = amend_after_leadership(self.logic[symbol], plan, leadership)
                if not leadership.approved:"""


def materialize_runner_source(source: str) -> str:
    order_occurrences = source.count(OLD_ORDER_BLOCK)
    if order_occurrences != 1:
        raise RuntimeError(
            "Candidate 13 order-boundary contract drifted: "
            f"expected one inherited bracket block, found {order_occurrences}",
        )
    post_gate_occurrences = source.count(OLD_POST_GATE_BLOCK)
    if post_gate_occurrences != 1:
        raise RuntimeError(
            "Candidate 13 post-gate contract drifted: "
            f"expected one leadership block, found {post_gate_occurrences}",
        )

    materialized = source.replace(OLD_ORDER_BLOCK, NEW_ORDER_BLOCK, 1)
    materialized = materialized.replace(OLD_POST_GATE_BLOCK, NEW_POST_GATE_BLOCK, 1)
    if materialized.count("candidate-13-market-parent") != 1:
        raise RuntimeError("Candidate 13 market-parent branch was not materialized exactly once")
    if materialized.count("candidate-13-post-gate") != 1:
        raise RuntimeError("Candidate 13 post-gate branch was not materialized exactly once")
    return materialized
