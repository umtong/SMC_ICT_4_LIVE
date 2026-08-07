#!/usr/bin/env python3
"""Patch frozen Candidate 11 with v30 equilibrium lifecycle and lower layers."""
from __future__ import annotations

import argparse
from pathlib import Path

from c10_v29_patch import patch as patch_v29


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected one marker, found {count}")
    return text.replace(old, new, 1)


def patch(path: Path) -> None:
    patch_v29(path)
    text = path.read_text(encoding="utf-8")

    text = replace_once(
        text,
        "from c10_v29_overlay import (\n",
        "from c10_v30_overlay import (\n"
        "    cost_neutral_stop,\n"
        "    equilibrium_enabled,\n"
        "    equilibrium_reached,\n"
        "    far_only_enabled,\n"
        "    source_midpoint,\n",
        "v30 overlay import",
    )
    text = replace_once(
        text,
        "from nautilus_trader.model.enums import AccountType, OmsType, OrderSide, OrderType, TimeInForce\n",
        "from nautilus_trader.model.enums import AccountType, OmsType, OrderSide, OrderType, TimeInForce, TriggerType\n",
        "TriggerType import",
    )
    text = replace_once(
        text,
        "            self.active_entry_order_id: str | None = None\n",
        "            self.active_entry_order_id: str | None = None\n"
        "            self.active_source_midpoint: float | None = None\n"
        "            self.equilibrium_armed = False\n"
        "            self.replacement_exit_mates: dict[Any, Any] = {}\n"
        "            self.replacement_exit_roles: dict[Any, str] = {}\n",
        "v30 lifecycle state",
    )

    text = replace_once(
        text,
        '''                if ts_ns < self.config.evaluation_start_ns:
                    self.logic[symbol].mark_rejected(plan, ts_ns, "OUTSIDE_EVALUATION_WINDOW")
                    self._capture_events(symbol)
                    continue
                leadership = self.leadership.decide(
''',
        '''                if ts_ns < self.config.evaluation_start_ns:
                    self.logic[symbol].mark_rejected(plan, ts_ns, "OUTSIDE_EVALUATION_WINDOW")
                    self._capture_events(symbol)
                    continue
                if far_only_enabled() and plan.scenario.value != "FAR":
                    self.logic[symbol].mark_rejected(
                        plan,
                        ts_ns,
                        "V30_FAR_ONLY_LINEAGE",
                    )
                    self._capture_events(symbol)
                    continue
                leadership = self.leadership.decide(
''',
        "FAR-only gate",
    )

    text = replace_once(
        text,
        '''            self.active_plan = plan
            self.active_symbol = symbol
            self.active_entry_order_id = entry_order_id
            self.active_cost_record = {
''',
        '''            self.active_plan = plan
            self.active_symbol = symbol
            self.active_entry_order_id = entry_order_id
            self.active_source_midpoint = source_midpoint(
                self.logic[symbol],
                plan.scenario_id,
            )
            self.equilibrium_armed = False
            self.replacement_exit_mates.clear()
            self.replacement_exit_roles.clear()
            self.active_cost_record = {
''',
        "source midpoint capture",
    )
    text = replace_once(
        text,
        '''                "last_exit_fill_ts_ns": 0,
            }
''',
        '''                "last_exit_fill_ts_ns": 0,
                "source_range_midpoint": self.active_source_midpoint,
                "equilibrium_protection_enabled": equilibrium_enabled(),
                "equilibrium_protection_armed": False,
                "cost_neutral_stop": None,
            }
''',
        "cost record equilibrium fields",
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

        def _maybe_arm_equilibrium_protection(self, symbol: str, bar: Bar) -> None:
            if (
                not equilibrium_enabled()
                or self.equilibrium_armed
                or self.active_plan is None
                or self.active_symbol != symbol
                or self.mutex.state != SlotState.POSITION_OPEN
                or self.active_plan.scenario.value != "FAR"
                or self.active_source_midpoint is None
                or self.active_cost_record is None
            ):
                return
            direction = self.active_plan.direction.value
            midpoint = float(self.active_source_midpoint)
            if not equilibrium_reached(
                direction=direction,
                high=float(str(bar.high)),
                low=float(str(bar.low)),
                midpoint=midpoint,
            ):
                return
            filled_qty = float(self.active_cost_record.get("entry_filled_qty", 0.0))
            if filled_qty <= 0.0:
                return
            instrument = instruments[symbol]
            entry = float(self.active_plan.expected_entry)
            impact = float(self.active_cost_record["impact_per_side"])
            neutral = cost_neutral_stop(
                direction=direction,
                entry_price=entry,
                maker_fee=float(execution_config["effective_maker_rate"]),
                taker_fee=float(execution_config["effective_taker_rate"]),
                impact_per_side=impact,
            )
            current = float(str(bar.close))
            target_value = float(self.active_plan.target_price)
            self.equilibrium_armed = True
            self.active_cost_record["equilibrium_protection_armed"] = True
            self.active_cost_record["equilibrium_observed_ts_ns"] = int(bar.ts_event)
            self.active_cost_record["cost_neutral_stop"] = neutral
            instrument_id = instrument.id
            self.cancel_all_orders(instrument_id)

            invalid_now = (
                current <= neutral if direction == "LONG" else current >= neutral
            )
            target_now = (
                current >= target_value if direction == "LONG" else current <= target_value
            )
            if invalid_now or target_now:
                self.lifecycle.append({
                    "type": "EQUILIBRIUM_PROTECTION_MARKET_EXIT",
                    "ts_event": int(bar.ts_event),
                    "scenario_id": self.active_plan.scenario_id,
                    "symbol": symbol,
                    "reason": (
                        "COST_NEUTRAL_LEVEL_ALREADY_REACCEPTED"
                        if invalid_now
                        else "EXTERNAL_TARGET_ALREADY_REACHED"
                    ),
                    "midpoint": midpoint,
                    "cost_neutral_stop": neutral,
                    "current": current,
                })
                self.close_all_positions(instrument_id)
                return

            quantity = instrument.make_qty(filled_qty)
            exit_side = OrderSide.SELL if direction == "LONG" else OrderSide.BUY
            stop_order = self.order_factory.stop_market(
                instrument_id=instrument_id,
                order_side=exit_side,
                quantity=quantity,
                trigger_price=instrument.make_price(neutral),
                trigger_type=TriggerType.LAST_PRICE,
                reduce_only=True,
                tags=["V30_COST_NEUTRAL_EQUILIBRIUM_STOP"],
            )
            target_order = self.order_factory.limit(
                instrument_id=instrument_id,
                order_side=exit_side,
                quantity=quantity,
                price=instrument.make_price(target_value),
                post_only=True,
                reduce_only=True,
                tags=["V30_EXTERNAL_DRAW_RUNNER_TARGET"],
            )
            self.replacement_exit_roles[stop_order.client_order_id] = "EQUILIBRIUM_STOP"
            self.replacement_exit_roles[target_order.client_order_id] = "EXTERNAL_TARGET"
            self._register_replacement_pair(stop_order, target_order)
            self.submit_order(stop_order)
            self.submit_order(target_order)
            self.lifecycle.append({
                "type": "EQUILIBRIUM_PROTECTION_ARMED",
                "ts_event": int(bar.ts_event),
                "scenario_id": self.active_plan.scenario_id,
                "symbol": symbol,
                "midpoint": midpoint,
                "cost_neutral_stop": neutral,
                "external_target": target_value,
                "quantity": filled_qty,
                "stop_client_order_id": str(stop_order.client_order_id),
                "target_client_order_id": str(target_order.client_order_id),
            })

'''
    text = replace_once(
        text,
        "        def on_bar(self, bar: Bar) -> None:\n",
        methods + "        def on_bar(self, bar: Bar) -> None:\n",
        "equilibrium methods",
    )
    text = replace_once(
        text,
        '''            symbol = self._symbol(bar)
            key = (str(bar.bar_type.instrument_id), self.last_ts_ns)
''',
        '''            symbol = self._symbol(bar)
            self._maybe_arm_equilibrium_protection(symbol, bar)
            key = (str(bar.bar_type.instrument_id), self.last_ts_ns)
''',
        "equilibrium on-bar hook",
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
        "replacement OCO fill hook",
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
        "replacement denied fail-close",
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
        "replacement rejected fail-close",
    )

    # Both pending-entry and position terminal branches already clear v27 cost
    # state. Extend those exact resets with v30 lifecycle state.
    reset_old = '''                    self.active_cost_record = None
                    self.active_entry_order_id = None
'''
    reset_new = '''                    self.active_cost_record = None
                    self.active_entry_order_id = None
                    self.active_source_midpoint = None
                    self.equilibrium_armed = False
                    self.replacement_exit_mates.clear()
                    self.replacement_exit_roles.clear()
'''
    count = text.count(reset_old)
    if count != 2:
        raise RuntimeError(f"v30 terminal reset: expected two markers, found {count}")
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
