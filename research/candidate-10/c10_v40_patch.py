#!/usr/bin/env python3
"""Patch frozen Candidate 11 with v40 detector/scenario separation."""
from __future__ import annotations

import argparse
from pathlib import Path

from c10_v38_patch import patch as patch_v38


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected one marker, found {count}")
    return text.replace(old, new, 1)


def patch(path: Path) -> None:
    patch_v38(path)
    text = path.read_text(encoding="utf-8")
    text = replace_once(
        text,
        "from c10_v38_overlay import (\n",
        "from c10_v40_overlay import (\n",
        "v40 overlay import",
    )
    text = replace_once(
        text,
        "from c10_v38_state import ConfirmedMicroPivotProtectionEngine as RegionalHandoffAuctionEngine\n",
        "from c10_v40_state import SourceEquilibriumFailedAuctionEngine as RegionalHandoffAuctionEngine\n",
        "v40 state-engine import",
    )
    text = replace_once(
        text,
        "            self.active_entry_order_id: str | None = None\n"
        "            self.internal_pivot_protection_armed = False\n",
        "            self.active_entry_order_id: str | None = None\n"
        "            self.protection_activation_fail_close_pending = False\n"
        "            self.internal_pivot_protection_armed = False\n",
        "v40 fail-close state",
    )
    text = replace_once(
        text,
        '''            self.active_symbol = symbol
            self.active_entry_order_id = entry_order_id
            self.internal_pivot_protection_armed = False
''',
        '''            self.active_symbol = symbol
            self.active_entry_order_id = entry_order_id
            self.protection_activation_fail_close_pending = False
            self.internal_pivot_protection_armed = False
''',
        "v40 submitted fail-close reset",
    )

    method = '''        def _fail_close_protection_activation(
            self,
            event: OrderEvent,
            kind: str,
        ) -> bool:
            if self.protection_activation_fail_close_pending:
                return True
            if (
                self.active_plan is None
                or self.active_symbol is None
                or self.mutex.state != SlotState.POSITION_OPEN
            ):
                return False
            instrument_id = instruments[self.active_symbol].id
            if self.portfolio.is_flat(instrument_id):
                return False
            self.protection_activation_fail_close_pending = True
            self.lifecycle.append({
                "type": "PROTECTIVE_ACTIVATION_FAIL_CLOSE_SUBMITTED",
                "ts_event": int(event.ts_event),
                "scenario_id": self.active_plan.scenario_id,
                "symbol": self.active_symbol,
                "rejected_client_order_id": str(event.client_order_id),
                "rejection_kind": kind,
                "event": str(event),
                "reason": (
                    "filled parent cannot remain open when a contingent "
                    "protective order is denied or rejected"
                ),
            })
            if self.cache.orders_open_count(
                instrument_id=instrument_id,
                strategy_id=self.id,
            ):
                self.cancel_all_orders(instrument_id)
            self.close_all_positions(instrument_id)
            return True

'''
    text = replace_once(
        text,
        '''        def on_order_denied(self, event: OrderEvent) -> None:
            self._record_order_event(event, "ORDER_DENIED")
            self.errors.append({"type": "ORDER_DENIED", "event": str(event)})
            self._fail_close_replacement_error(event)
            self._release_if_terminal(int(event.ts_event), "ORDER_DENIED")
''',
        method + '''        def on_order_denied(self, event: OrderEvent) -> None:
            self._record_order_event(event, "ORDER_DENIED")
            controlled = self._fail_close_protection_activation(
                event,
                "ORDER_DENIED",
            )
            if not controlled:
                self.errors.append({
                    "type": "ORDER_DENIED",
                    "event": str(event),
                })
                self._fail_close_replacement_error(event)
            self._release_if_terminal(int(event.ts_event), "ORDER_DENIED")
''',
        "v40 denied fail-close",
    )
    text = replace_once(
        text,
        '''        def on_order_rejected(self, event: OrderEvent) -> None:
            self._record_order_event(event, "ORDER_REJECTED")
            self.errors.append({"type": "ORDER_REJECTED", "event": str(event)})
            self._fail_close_replacement_error(event)
            self._release_if_terminal(int(event.ts_event), "ORDER_REJECTED")
''',
        '''        def on_order_rejected(self, event: OrderEvent) -> None:
            self._record_order_event(event, "ORDER_REJECTED")
            controlled = self._fail_close_protection_activation(
                event,
                "ORDER_REJECTED",
            )
            if not controlled:
                self.errors.append({
                    "type": "ORDER_REJECTED",
                    "event": str(event),
                })
                self._fail_close_replacement_error(event)
            self._release_if_terminal(int(event.ts_event), "ORDER_REJECTED")
''',
        "v40 rejected fail-close",
    )

    reset_old = '''                    self.active_entry_order_id = None
                    self.internal_pivot_protection_armed = False
'''
    reset_new = '''                    self.active_entry_order_id = None
                    self.protection_activation_fail_close_pending = False
                    self.internal_pivot_protection_armed = False
'''
    count = text.count(reset_old)
    if count != 2:
        raise RuntimeError(
            f"v40 terminal fail-close reset: expected two markers, found {count}",
        )
    text = text.replace(reset_old, reset_new)
    path.write_text(text, encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("path", type=Path)
    args = parser.parse_args()
    patch(args.path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
