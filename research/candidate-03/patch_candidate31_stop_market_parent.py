#!/usr/bin/env python3
"""Add a native STOP_MARKET parent branch to the frozen runner materializer.

Candidate 31 pre-positions an entry trigger after the completed opening minute.
This patch changes no fill model: it only maps an explicit TradePlan parent type
to NautilusTrader's bracket factory. The existing MARKET and LIMIT branches are
left byte-for-byte intact apart from insertion of the new ``elif`` branch.
"""
from __future__ import annotations

import argparse
from pathlib import Path

MARKER = "candidate-31-native-stop-market-parent"
ANCHOR = '''                    )
                else:
                    order_list = self.order_factory.bracket(
                        instrument_id=instrument.id,
                        order_side=side,
                        quantity=instrument.make_qty(decision.quantity),
                        entry_order_type=OrderType.LIMIT,
'''
REPLACEMENT = '''                    )
                elif plan.entry_order_type == "STOP_MARKET":
                    # candidate-31-native-stop-market-parent: trigger and fill remain native.
                    order_list = self.order_factory.bracket(
                        instrument_id=instrument.id,
                        order_side=side,
                        quantity=instrument.make_qty(decision.quantity),
                        entry_order_type=OrderType.STOP_MARKET,
                        entry_trigger_price=instrument.make_price(plan.expected_entry),
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
'''


def apply(path: Path) -> bool:
    source = path.read_text(encoding="utf-8")
    if MARKER in source:
        return False
    count = source.count(ANCHOR)
    if count != 1:
        raise RuntimeError(
            f"Candidate 31 runner materializer anchor drifted: found {count}",
        )
    source = source.replace(ANCHOR, REPLACEMENT, 1)
    if source.count(MARKER) != 1:
        raise RuntimeError("Candidate 31 STOP_MARKET branch was not inserted once")
    path.write_text(source, encoding="utf-8")
    return True


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("path", type=Path)
    args = parser.parse_args()
    print(f"candidate31 STOP_MARKET parent patch applied={apply(args.path)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
