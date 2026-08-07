#!/usr/bin/env python3
"""Classify real Nautilus bracket exits before releasing the global slot.

The frozen runner previously forwarded every fill as ``ORDER_FILLED``. Candidate
16 needs only one additional piece of native execution evidence: whether a
completely closed position ended through the STOP_MARKET child or the LIMIT
profit child. Entry fills, partial fills, parent expiry flattening and end-of-run
market closes retain their existing behavior.
"""
from __future__ import annotations

import argparse
from pathlib import Path

OLD = '''        def on_order_filled(self, event: OrderEvent) -> None:\n            self._record_order_event(event, "ORDER_FILLED")\n            self._release_if_terminal(int(event.ts_event), "ORDER_FILLED")\n'''
NEW = '''        def on_order_filled(self, event: OrderEvent) -> None:\n            self._record_order_event(event, "ORDER_FILLED")\n            reason = "ORDER_FILLED"\n            if (\n                self.active_plan is not None\n                and self.active_symbol is not None\n                and self.mutex.state == SlotState.POSITION_OPEN\n            ):\n                order_type = str(getattr(event, "order_type", "")).upper()\n                order_side = str(getattr(event, "order_side", "")).upper()\n                exit_side = (\n                    "SELL"\n                    if self.active_plan.direction.value == "LONG"\n                    else "BUY"\n                )\n                if order_side.endswith(exit_side):\n                    if order_type.endswith("STOP_MARKET"):\n                        reason = "STOP_MARKET_FILLED"\n                    elif order_type.endswith("LIMIT"):\n                        reason = "TARGET_LIMIT_FILLED"\n                if reason != "ORDER_FILLED":\n                    self.lifecycle.append({\n                        "type": "NATIVE_EXIT_CLASSIFIED",\n                        "ts_event": int(event.ts_event),\n                        "scenario_id": self.active_plan.scenario_id,\n                        "symbol": self.active_symbol,\n                        "reason": reason,\n                        "order_type": order_type,\n                        "order_side": order_side,\n                        "last_px": str(getattr(event, "last_px", "")),\n                    })\n            self._release_if_terminal(int(event.ts_event), reason)\n'''


def apply(path: Path) -> bool:
    source = path.read_text(encoding="utf-8")
    if NEW in source:
        return False
    if source.count(OLD) != 1:
        raise RuntimeError(f"expected one on_order_filled block, found {source.count(OLD)}")
    path.write_text(source.replace(OLD, NEW, 1), encoding="utf-8")
    return True


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("path", type=Path)
    args = parser.parse_args()
    print(f"candidate16 native exit-reason patch applied={apply(args.path)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
