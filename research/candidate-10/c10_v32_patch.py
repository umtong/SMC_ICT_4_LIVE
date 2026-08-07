#!/usr/bin/env python3
"""Patch frozen Candidate 11 with v32 funded partial risk transfer."""
from __future__ import annotations

import argparse
from pathlib import Path

from c10_v30_patch import patch as patch_v30


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected one marker, found {count}")
    return text.replace(old, new, 1)


def patch(path: Path) -> None:
    patch_v30(path)
    text = path.read_text(encoding="utf-8")
    text = replace_once(
        text,
        "from c10_v30_overlay import (\n",
        "from c10_v32_overlay import (\n"
        "    funded_partial_enabled,\n"
        "    solve_funded_reduction,\n",
        "v32 overlay import",
    )

    start_marker = "        def _maybe_arm_equilibrium_protection"
    end_marker = "\n        def on_bar"
    start = text.find(start_marker)
    if start < 0:
        raise RuntimeError("v32 equilibrium method start marker missing")
    end = text.find(end_marker, start)
    if end < 0:
        raise RuntimeError("v32 equilibrium method end marker missing")

    method = '''        def _maybe_arm_equilibrium_protection(self, symbol: str, bar: Bar) -> None:
            if (
                not equilibrium_enabled()
                or not funded_partial_enabled()
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
            current = float(str(bar.close))
            # Risk is transferred only after a completed bar closes through the
            # pre-existing source equilibrium. Intrabar touches do not qualify.
            if not equilibrium_reached(
                direction=direction,
                high=current,
                low=current,
                midpoint=midpoint,
            ):
                return

            total_quantity = Decimal(
                str(self.active_cost_record.get("entry_filled_qty", 0.0)),
            )
            planned_quantity = Decimal(
                str(self.active_cost_record.get("quantity", 0.0)),
            )
            expected_total_loss = Decimal(
                str(self.active_cost_record.get("expected_total_loss", 0.0)),
            )
            if total_quantity <= 0 or planned_quantity <= 0 or expected_total_loss <= 0:
                return
            instrument = instruments[symbol]
            reduction = solve_funded_reduction(
                direction=direction,
                total_quantity=total_quantity,
                entry_price=Decimal(str(self.active_plan.expected_entry)),
                current_price=Decimal(str(current)),
                original_loss_per_unit=expected_total_loss / planned_quantity,
                maker_fee=Decimal(str(execution_config["effective_maker_rate"])),
                taker_fee=Decimal(str(execution_config["effective_taker_rate"])),
                impact_per_side=Decimal(str(self.active_cost_record["impact_per_side"])),
                tick_size=Decimal(str(instrument.price_increment)),
                quantity_increment=Decimal(str(instrument.size_increment)),
                min_quantity=Decimal(str(instrument.min_quantity)),
            )
            if reduction is None:
                return

            target_value = float(self.active_plan.target_price)
            target_now = (
                current >= target_value if direction == "LONG" else current <= target_value
            )
            self.equilibrium_armed = True
            self.active_cost_record["equilibrium_protection_armed"] = True
            self.active_cost_record["equilibrium_observed_ts_ns"] = int(bar.ts_event)
            self.active_cost_record["funded_partial_fraction"] = float(reduction.fraction)
            self.active_cost_record["funded_partial_quantity"] = float(reduction.partial_quantity)
            self.active_cost_record["funded_residual_quantity"] = float(reduction.residual_quantity)
            self.active_cost_record["funded_expected_exit_price"] = float(reduction.expected_exit_price)
            self.active_cost_record["funded_gain_per_unit"] = float(reduction.gain_per_unit)
            self.active_cost_record["funded_locked_profit"] = float(reduction.locked_profit)
            self.active_cost_record["funded_residual_max_loss"] = float(reduction.residual_max_loss)

            instrument_id = instrument.id
            self.cancel_all_orders(instrument_id)
            if target_now:
                self.lifecycle.append({
                    "type": "SOURCE_EQUILIBRIUM_TARGET_ALREADY_REACHED",
                    "ts_event": int(bar.ts_event),
                    "scenario_id": self.active_plan.scenario_id,
                    "symbol": symbol,
                    "midpoint": midpoint,
                    "current": current,
                })
                self.close_all_positions(instrument_id)
                return

            exit_side = OrderSide.SELL if direction == "LONG" else OrderSide.BUY
            partial_order = self.order_factory.market(
                instrument_id=instrument_id,
                order_side=exit_side,
                quantity=instrument.make_qty(reduction.partial_quantity),
                reduce_only=True,
                tags=["V32_FUNDED_SOURCE_EQUILIBRIUM_REDUCTION"],
            )
            self.replacement_exit_roles[partial_order.client_order_id] = "FUNDED_PARTIAL"
            self.submit_order(partial_order)

            if reduction.residual_quantity > 0:
                stop_order = self.order_factory.stop_market(
                    instrument_id=instrument_id,
                    order_side=exit_side,
                    quantity=instrument.make_qty(reduction.residual_quantity),
                    trigger_price=instrument.make_price(self.active_plan.stop_price),
                    trigger_type=TriggerType.LAST_PRICE,
                    reduce_only=True,
                    tags=["V32_RESIDUAL_ORIGINAL_INVALIDATION"],
                )
                target_order = self.order_factory.limit(
                    instrument_id=instrument_id,
                    order_side=exit_side,
                    quantity=instrument.make_qty(reduction.residual_quantity),
                    price=instrument.make_price(target_value),
                    post_only=True,
                    reduce_only=True,
                    tags=["V32_RESIDUAL_EXTERNAL_DRAW_TARGET"],
                )
                self.replacement_exit_roles[stop_order.client_order_id] = "RESIDUAL_STOP"
                self.replacement_exit_roles[target_order.client_order_id] = "RESIDUAL_TARGET"
                self._register_replacement_pair(stop_order, target_order)
                self.submit_order(stop_order)
                self.submit_order(target_order)
                stop_id = str(stop_order.client_order_id)
                target_id = str(target_order.client_order_id)
            else:
                stop_id = None
                target_id = None

            self.lifecycle.append({
                "type": "FUNDED_SOURCE_EQUILIBRIUM_REDUCTION_SUBMITTED",
                "ts_event": int(bar.ts_event),
                "scenario_id": self.active_plan.scenario_id,
                "symbol": symbol,
                "midpoint": midpoint,
                "current": current,
                "partial_quantity": float(reduction.partial_quantity),
                "residual_quantity": float(reduction.residual_quantity),
                "fraction": float(reduction.fraction),
                "locked_profit": float(reduction.locked_profit),
                "residual_max_loss": float(reduction.residual_max_loss),
                "partial_client_order_id": str(partial_order.client_order_id),
                "stop_client_order_id": stop_id,
                "target_client_order_id": target_id,
            })
'''
    text = text[:start] + method + text[end:]

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
        "v32 expired fail-close",
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
        "v32 canceled fail-close",
    )
    path.write_text(text, encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("path", type=Path)
    args = parser.parse_args()
    patch(args.path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
