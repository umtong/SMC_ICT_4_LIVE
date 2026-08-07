#!/usr/bin/env python3
"""Preserve real Nautilus bracket-exit identity until the global slot releases.

A bracket exit fill is followed by cancellation of the sibling child. The
frozen runner therefore releases the position on the later cancellation event,
not necessarily inside ``on_order_filled``. Candidate 16 stores the native
child identity at the fill and consumes it only when the account is flat and no
orders remain.

The patch is diagnostic/state-routing infrastructure only. It changes no order,
fill, price, cost, quantity, risk budget, target or stop.
"""
from __future__ import annotations

import argparse
from pathlib import Path

OLD_RELEASE = '''            elif self.mutex.state == SlotState.POSITION_OPEN:\n                if self._all_flat() and self._open_orders() == 0:\n                    self.mutex.mark_position_closed(scenario_id)\n                    self.logic[self.active_symbol].mark_trade_terminal(ts_ns, reason)\n                    self._capture_events(self.active_symbol)\n                    self.lifecycle.append({\n                        "type": "GLOBAL_POSITION_CLOSED",\n                        "ts_event": ts_ns,\n                        "scenario_id": scenario_id,\n                        "symbol": self.active_symbol,\n                    })\n                    self.active_plan = None\n                    self.active_symbol = None\n'''
NEW_RELEASE = '''            elif self.mutex.state == SlotState.POSITION_OPEN:\n                if self._all_flat() and self._open_orders() == 0:\n                    terminal_reason = (\n                        getattr(self, "_candidate16_pending_exit_reason", None)\n                        or reason\n                    )\n                    self.mutex.mark_position_closed(scenario_id)\n                    self.logic[self.active_symbol].mark_trade_terminal(ts_ns, terminal_reason)\n                    self._capture_events(self.active_symbol)\n                    self.lifecycle.append({\n                        "type": "GLOBAL_POSITION_CLOSED",\n                        "ts_event": ts_ns,\n                        "scenario_id": scenario_id,\n                        "symbol": self.active_symbol,\n                        "reason": terminal_reason,\n                    })\n                    self._candidate16_pending_exit_reason = None\n                    self.active_plan = None\n                    self.active_symbol = None\n'''

OLD_FILLED = '''        def on_order_filled(self, event: OrderEvent) -> None:\n            self._record_order_event(event, "ORDER_FILLED")\n            self._release_if_terminal(int(event.ts_event), "ORDER_FILLED")\n'''
NEW_FILLED = '''        def on_order_filled(self, event: OrderEvent) -> None:\n            self._record_order_event(event, "ORDER_FILLED")\n            event_text = str(event)\n            reason = "ORDER_FILLED"\n            if (\n                self.active_plan is not None\n                and self.active_symbol is not None\n                and self.mutex.state == SlotState.POSITION_OPEN\n            ):\n                exit_side = (\n                    "SELL"\n                    if self.active_plan.direction.value == "LONG"\n                    else "BUY"\n                )\n                is_exit_side = f"order_side={exit_side}" in event_text\n                if is_exit_side and "order_type=STOP_MARKET" in event_text:\n                    reason = "STOP_MARKET_FILLED"\n                elif is_exit_side and "order_type=LIMIT" in event_text:\n                    reason = "TARGET_LIMIT_FILLED"\n                if reason != "ORDER_FILLED":\n                    self._candidate16_pending_exit_reason = reason\n                    self.lifecycle.append({\n                        "type": "NATIVE_EXIT_CLASSIFIED",\n                        "ts_event": int(event.ts_event),\n                        "scenario_id": self.active_plan.scenario_id,\n                        "symbol": self.active_symbol,\n                        "reason": reason,\n                        "client_order_id": str(event.client_order_id),\n                        "event": event_text,\n                    })\n            self._release_if_terminal(int(event.ts_event), reason)\n'''


def _replace_once(source: str, old: str, new: str, label: str) -> tuple[str, bool]:
    if new in source:
        return source, False
    count = source.count(old)
    if count != 1:
        raise RuntimeError(f"expected one {label} block, found {count}")
    return source.replace(old, new, 1), True


def apply(path: Path) -> bool:
    source = path.read_text(encoding="utf-8")
    source, changed_release = _replace_once(source, OLD_RELEASE, NEW_RELEASE, "position-release")
    source, changed_filled = _replace_once(source, OLD_FILLED, NEW_FILLED, "on_order_filled")
    path.write_text(source, encoding="utf-8")
    return changed_release or changed_filled


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("path", type=Path)
    args = parser.parse_args()
    print(f"candidate16 native exit-reason patch applied={apply(args.path)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
