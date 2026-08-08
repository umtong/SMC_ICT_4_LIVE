#!/usr/bin/env python3
"""Materialize a native GTD STOP_MARKET parent for Candidate 31."""
from __future__ import annotations

import argparse
from pathlib import Path

MARKER = "candidate-31-native-gtd-stop-market-parent"
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
                    # candidate-31-native-gtd-stop-market-parent: Nautilus owns trigger/fill.
                    order_list = self.order_factory.bracket(
                        instrument_id=instrument.id,
                        order_side=side,
                        quantity=instrument.make_qty(decision.quantity),
                        entry_order_type=OrderType.STOP_MARKET,
                        entry_trigger_price=instrument.make_price(plan.expected_entry),
                        expire_time=datetime.fromtimestamp(plan.expire_ts_ns / 1_000_000_000, tz=UTC) + timedelta(microseconds=1),
                        time_in_force=TimeInForce.GTD,
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
        raise RuntimeError("Candidate 31 GTD STOP_MARKET branch was not inserted once")
    path.write_text(source, encoding="utf-8")
    return True


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("path", type=Path)
    args = parser.parse_args()
    print(f"candidate31 GTD STOP_MARKET parent patch applied={apply(args.path)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
