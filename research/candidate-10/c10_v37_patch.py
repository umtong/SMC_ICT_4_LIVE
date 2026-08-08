#!/usr/bin/env python3
"""Patch frozen Candidate 11 with v37 internal-pivot protection."""
from __future__ import annotations

import argparse
from pathlib import Path

from c10_v36_patch import patch as patch_v36


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected one marker, found {count}")
    return text.replace(old, new, 1)


def patch(path: Path) -> None:
    patch_v36(path)
    text = path.read_text(encoding="utf-8")

    text = replace_once(
        text,
        "from c10_v36_overlay import (\n",
        "from c10_v37_overlay import (\n"
        "    first_favorable_internal_pivot,\n"
        "    internal_pivot_protection_enabled,\n",
        "v37 overlay import",
    )
    text = replace_once(
        text,
        "from c10_v36_state import ConsequentEncroachmentRejectionEngine as RegionalHandoffAuctionEngine\n",
        "from c10_v37_state import ConfirmedInternalPivotProtectionEngine as RegionalHandoffAuctionEngine\n",
        "v37 state-engine import",
    )
    text = replace_once(
        text,
        "from nautilus_trader.model.enums import AccountType, OmsType, OrderSide, OrderType, TimeInForce\n",
        "from nautilus_trader.model.enums import AccountType, OmsType, OrderSide, OrderType, TimeInForce, TriggerType\n",
        "v37 TriggerType import",
    )
    text = replace_once(
        text,
        "            self.active_entry_order_id: str | None = None\n",
        "            self.active_entry_order_id: str | None = None\n"
        "            self.internal_pivot_protection_armed = False\n"
        "            self.replacement_exit_mates: dict[Any, Any] = {}\n"
        "            self.replacement_exit_roles: dict[Any, str] = {}\n",
        "v37 lifecycle state",
    )
    text = replace_once(
        text,
        '''            self.active_symbol = symbol
            self.active_entry_order_id = entry_order_id
            self.active_cost_record = {
''',
        '''            self.active_symbol = symbol
            self.active_entry_order_id = entry_order_id
            self.internal_pivot_protection_armed = False
            self.replacement_exit_mates.clear()
            self.replacement_exit_roles.clear()
            self.active_cost_record = {
''',
        "v37 active-state reset",
    )
    text = replace_once(
        text,
        '''                "last_exit_fill_ts_ns": 0,
            }
''',
        '''                "last_exit_fill_ts_ns": 0,
                "internal_pivot_protection_enabled": (
                    internal_pivot_protection_enabled()
                ),
                "internal_pivot_protection_armed": False,
                "internal_pivot_event_ts_ns": 0,
                "internal_pivot_known_ts_ns": 0,
                "internal_pivot_level": None,
                "internal_pivot_reference_extreme": None,
                "internal_pivot_protective_stop": None,
            }
''',
        "v37 cost-record fields",
    )

    methods = '''        @staticmethod
        def _order_is_open(order: Any) -> bool:
            value = getattr(order, "is_open", False)
            return bool(value() if callable(value) else value)

        def _cancel_order_id(self, client_order_id: Any | None) -> None:
            if client_order_id is None:
                return
            order = self.cache.order(client_order_id)
            if order is not None and self._order_is_open(order):
                self.cancel_order(order)

        def _register_replacement_pair(self, first: Any, second: Any) -> None:
            self.replacement_exit_mates[first.client_order_id] = second.client_order_id
            self.replacement_exit_mates[second.client_order_id] = first.client_order_id

        def _fail_close_replacement_error(self, event: OrderEvent) -> None:
            client_order_id = getattr(event, "client_order_id", None)
            if client_order_id not in self.replacement_exit_roles:
                return
            mate = self.replacement_exit_mates.pop(client_order_id, None)
            if mate is not None:
                self.replacement_exit_mates.pop(mate, None)
                self._cancel_order_id(mate)
                self.replacement_exit_roles.pop(mate, None)
            self.replacement_exit_roles.pop(client_order_id, None)
            if self.active_symbol is not None:
                instrument_id = instruments[self.active_symbol].id
                self.cancel_all_orders(instrument_id)
                if not self.portfolio.is_flat(instrument_id):
                    self.close_all_positions(instrument_id)

        def _maybe_transfer_internal_pivot(
            self,
            symbol: str,
            observation: BarObs,
        ) -> None:
            if (
                not internal_pivot_protection_enabled()
                or self.internal_pivot_protection_armed
                or self.active_plan is None
                or self.active_symbol != symbol
                or self.active_cost_record is None
                or self.mutex.state != SlotState.POSITION_OPEN
                or self.active_plan.scenario.value != "FAR"
            ):
                return
            filled_qty = float(
                self.active_cost_record.get("entry_filled_qty", 0.0),
            )
            entry_fill_ts_ns = int(
                self.active_cost_record.get("first_entry_fill_ts_ns", 0),
            )
            if filled_qty <= 0.0 or entry_fill_ts_ns <= 0:
                return

            logic = self.logic[symbol]
            instrument = instruments[symbol]
            decision = first_favorable_internal_pivot(
                direction=self.active_plan.direction.value,
                internal_highs=logic.internal_highs,
                internal_lows=logic.internal_lows,
                entry_fill_ts_ns=entry_fill_ts_ns,
                observed_ts_ns=observation.ts_ns,
                original_stop=float(self.active_plan.stop_price),
                reference_extreme=float(
                    self.active_plan.details["ce_rejection_primary"][
                        "retest_extreme"
                    ],
                ),
                current_price=float(observation.close),
                target_price=float(self.active_plan.target_price),
                atr=float(self.active_plan.atr),
                stop_buffer_atr=float(logic.config.stop_buffer_atr),
                tick_size=float(str(instrument.price_increment)),
            )
            if decision is None:
                return

            self.internal_pivot_protection_armed = True
            self.active_cost_record["internal_pivot_protection_armed"] = True
            self.active_cost_record["internal_pivot_event_ts_ns"] = (
                decision.pivot_event_ts_ns
            )
            self.active_cost_record["internal_pivot_known_ts_ns"] = (
                decision.pivot_known_ts_ns
            )
            self.active_cost_record["internal_pivot_level"] = decision.pivot_level
            self.active_cost_record["internal_pivot_reference_extreme"] = (
                decision.reference_extreme
            )
            self.active_cost_record["internal_pivot_protective_stop"] = (
                decision.protective_stop
            )

            instrument_id = instrument.id
            self.cancel_all_orders(instrument_id)
            exit_side = (
                OrderSide.SELL
                if decision.direction == "LONG"
                else OrderSide.BUY
            )
            quantity = instrument.make_qty(filled_qty)
            try:
                stop_order = self.order_factory.stop_market(
                    instrument_id=instrument_id,
                    order_side=exit_side,
                    quantity=quantity,
                    trigger_price=instrument.make_price(
                        decision.protective_stop,
                    ),
                    trigger_type=TriggerType.LAST_PRICE,
                    reduce_only=True,
                    tags=["V37_CONFIRMED_INTERNAL_PIVOT_STOP"],
                )
                target_order = self.order_factory.limit(
                    instrument_id=instrument_id,
                    order_side=exit_side,
                    quantity=quantity,
                    price=instrument.make_price(
                        self.active_plan.target_price,
                    ),
                    post_only=True,
                    reduce_only=True,
                    tags=["V37_SOURCE_EQUILIBRIUM_PRIMARY_TARGET"],
                )
                self.replacement_exit_roles[stop_order.client_order_id] = (
                    "INTERNAL_PIVOT_STOP"
                )
                self.replacement_exit_roles[target_order.client_order_id] = (
                    "SOURCE_EQUILIBRIUM_TARGET"
                )
                self._register_replacement_pair(stop_order, target_order)
                self.submit_order(stop_order)
                self.submit_order(target_order)
            except Exception as exc:
                self.errors.append({
                    "type": "V37_REPLACEMENT_SUBMISSION_EXCEPTION",
                    "ts_ns": int(observation.ts_ns),
                    "symbol": symbol,
                    "exception": type(exc).__name__,
                    "message": str(exc),
                })
                self.cancel_all_orders(instrument_id)
                if not self.portfolio.is_flat(instrument_id):
                    self.close_all_positions(instrument_id)
                return

            logic.mark_internal_pivot_protected(
                observed_ts_ns=decision.pivot_known_ts_ns,
                pivot_event_ts_ns=decision.pivot_event_ts_ns,
                direction=decision.direction,
                pivot_level=decision.pivot_level,
                reference_extreme=decision.reference_extreme,
                protective_stop=decision.protective_stop,
                original_stop=decision.original_stop,
            )
            self._capture_events(symbol)
            self.lifecycle.append({
                "type": "CONFIRMED_INTERNAL_PIVOT_PROTECTION_ARMED",
                "ts_event": int(observation.ts_ns),
                "scenario_id": self.active_plan.scenario_id,
                "symbol": symbol,
                "direction": decision.direction,
                "pivot_event_ts_ns": decision.pivot_event_ts_ns,
                "pivot_known_ts_ns": decision.pivot_known_ts_ns,
                "pivot_level": decision.pivot_level,
                "reference_extreme": decision.reference_extreme,
                "original_stop": decision.original_stop,
                "protective_stop": decision.protective_stop,
                "current_price": decision.current_price,
                "target_price": decision.target_price,
                "quantity": filled_qty,
                "stop_client_order_id": str(stop_order.client_order_id),
                "target_client_order_id": str(target_order.client_order_id),
            })

'''
    text = replace_once(
        text,
        "        def _process_batch(self, ts_ns: int) -> None:\n",
        methods + "        def _process_batch(self, ts_ns: int) -> None:\n",
        "v37 risk-transfer methods",
    )
    text = replace_once(
        text,
        '''                plan = self.logic[symbol].on_bar(observation)
                self._capture_events(symbol)
                if plan is None:
''',
        '''                plan = self.logic[symbol].on_bar(observation)
                self._capture_events(symbol)
                self._maybe_transfer_internal_pivot(symbol, observation)
                if plan is None:
''',
        "v37 synchronized post-structure hook",
    )

    text = replace_once(
        text,
        '''            self._release_if_terminal(int(event.ts_event), "ORDER_FILLED")
''',
        '''            replacement_mate = self.replacement_exit_mates.pop(
                event.client_order_id,
                None,
            )
            if replacement_mate is not None:
                self.replacement_exit_mates.pop(replacement_mate, None)
                self._cancel_order_id(replacement_mate)
                self.replacement_exit_roles.pop(replacement_mate, None)
            self.replacement_exit_roles.pop(event.client_order_id, None)
            self._release_if_terminal(int(event.ts_event), "ORDER_FILLED")
''',
        "v37 replacement OCO fill hook",
    )
    text = replace_once(
        text,
        '''        def on_order_expired(self, event: OrderEvent) -> None:
            self._record_order_event(event, "ORDER_EXPIRED")
            self._fail_close_partial_entry(event)
            self._release_if_terminal(int(event.ts_event), "ENTRY_EXPIRED")
''',
        '''        def on_order_expired(self, event: OrderEvent) -> None:
            self._record_order_event(event, "ORDER_EXPIRED")
            self._fail_close_replacement_error(event)
            self._fail_close_partial_entry(event)
            self._release_if_terminal(int(event.ts_event), "ENTRY_EXPIRED")
''',
        "v37 expired fail-close",
    )
    text = replace_once(
        text,
        '''        def on_order_canceled(self, event: OrderEvent) -> None:
            self._record_order_event(event, "ORDER_CANCELED")
            self._release_if_terminal(int(event.ts_event), "ORDER_CANCELED")
''',
        '''        def on_order_canceled(self, event: OrderEvent) -> None:
            self._record_order_event(event, "ORDER_CANCELED")
            self._fail_close_replacement_error(event)
            self._release_if_terminal(int(event.ts_event), "ORDER_CANCELED")
''',
        "v37 canceled fail-close",
    )
    text = replace_once(
        text,
        '''        def on_order_denied(self, event: OrderEvent) -> None:
            self._record_order_event(event, "ORDER_DENIED")
            self.errors.append({"type": "ORDER_DENIED", "event": str(event)})
            self._release_if_terminal(int(event.ts_event), "ORDER_DENIED")
''',
        '''        def on_order_denied(self, event: OrderEvent) -> None:
            self._record_order_event(event, "ORDER_DENIED")
            self.errors.append({"type": "ORDER_DENIED", "event": str(event)})
            self._fail_close_replacement_error(event)
            self._release_if_terminal(int(event.ts_event), "ORDER_DENIED")
''',
        "v37 denied fail-close",
    )
    text = replace_once(
        text,
        '''        def on_order_rejected(self, event: OrderEvent) -> None:
            self._record_order_event(event, "ORDER_REJECTED")
            self.errors.append({"type": "ORDER_REJECTED", "event": str(event)})
            self._release_if_terminal(int(event.ts_event), "ORDER_REJECTED")
''',
        '''        def on_order_rejected(self, event: OrderEvent) -> None:
            self._record_order_event(event, "ORDER_REJECTED")
            self.errors.append({"type": "ORDER_REJECTED", "event": str(event)})
            self._fail_close_replacement_error(event)
            self._release_if_terminal(int(event.ts_event), "ORDER_REJECTED")
''',
        "v37 rejected fail-close",
    )

    reset_old = '''                    self.active_cost_record = None
                    self.active_entry_order_id = None
'''
    reset_new = '''                    self.active_cost_record = None
                    self.active_entry_order_id = None
                    self.internal_pivot_protection_armed = False
                    self.replacement_exit_mates.clear()
                    self.replacement_exit_roles.clear()
'''
    count = text.count(reset_old)
    if count != 2:
        raise RuntimeError(
            f"v37 terminal reset: expected two markers, found {count}",
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
