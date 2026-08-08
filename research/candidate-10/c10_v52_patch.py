#!/usr/bin/env python3
"""Patch v51 with the v52 funded source-equilibrium external runner."""
from __future__ import annotations

import argparse
from pathlib import Path

from c10_v51_patch import patch as patch_v51


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected one marker, found {count}")
    return text.replace(old, new, 1)


def patch(path: Path) -> None:
    patch_v51(path)
    text = path.read_text(encoding="utf-8")
    text = replace_once(
        text,
        "from c10_v51_overlay import (\n",
        "from c10_v52_overlay import (\n"
        "    external_runner_enabled,\n"
        "    funded_equilibrium_runner_enabled,\n"
        "    reframe_external_runner,\n"
        "    solve_funded_reduction,\n",
        "v52 overlay import",
    )

    text = replace_once(
        text,
        "            self.active_entry_order_id: str | None = None\n"
        "            self.protection_activation_fail_close_pending = False\n"
        "            self.void_close_exit_triggered = False\n",
        "            self.active_entry_order_id: str | None = None\n"
        "            self.protection_activation_fail_close_pending = False\n"
        "            self.void_close_exit_triggered = False\n"
        "            self.external_runner_funded = False\n"
        "            self.active_source_equilibrium_checkpoint: float | None = None\n",
        "v52 lifecycle state",
    )
    text = replace_once(
        text,
        '''            self.active_entry_order_id = entry_order_id
            self.protection_activation_fail_close_pending = False
            self.void_close_exit_triggered = False
            self.internal_pivot_protection_armed = False
''',
        '''            self.active_entry_order_id = entry_order_id
            self.protection_activation_fail_close_pending = False
            self.void_close_exit_triggered = False
            self.external_runner_funded = False
            checkpoint_raw = plan.details.get(
                "source_equilibrium_checkpoint"
            )
            self.active_source_equilibrium_checkpoint = (
                None if checkpoint_raw is None else float(checkpoint_raw)
            )
            self.internal_pivot_protection_armed = False
''',
        "v52 submitted-state reset",
    )
    text = replace_once(
        text,
        '''                "void_close_exit_signed_distance": None,
''',
        '''                "void_close_exit_signed_distance": None,
                "external_runner_enabled": external_runner_enabled(),
                "funded_equilibrium_runner_enabled": (
                    funded_equilibrium_runner_enabled()
                ),
                "source_equilibrium_checkpoint": (
                    self.active_source_equilibrium_checkpoint
                ),
                "external_runner_funded": False,
                "funded_equilibrium_observed_ts_ns": 0,
                "funded_equilibrium_partial_fraction": None,
                "funded_equilibrium_partial_quantity": None,
                "funded_equilibrium_residual_quantity": None,
                "funded_equilibrium_locked_profit": None,
                "funded_equilibrium_residual_max_loss": None,
''',
        "v52 cost-record fields",
    )

    reframe = '''                runner = reframe_external_runner(
                    plan,
                    self.logic[symbol],
                )
                if not runner.approved:
                    self.logic[symbol].mark_rejected(
                        plan,
                        ts_ns,
                        runner.reason,
                        runner.details,
                    )
                    self._capture_events(symbol)
                    self.rejections.append({
                        "type": "EXTERNAL_RUNNER_REJECTED",
                        "observed_ts_ns": plan.observed_ts_ns,
                        "scenario_id": plan.scenario_id,
                        "symbol": symbol,
                        "direction": plan.direction.value,
                        "reason": runner.reason,
                        "details": runner.details,
                    })
                    continue
                plan = runner.plan
                instrument = instruments[symbol]
'''
    text = replace_once(
        text,
        "                instrument = instruments[symbol]\n"
        "                nav, free_balance = self._account_values()\n",
        reframe + "                nav, free_balance = self._account_values()\n",
        "v52 external target reframe",
    )

    methods = '''        def _cancel_pending_entry_after_equilibrium_delivery(
            self,
            symbol: str,
            observation: BarObs,
        ) -> bool:
            """Cancel a passive parent once the first delivery already occurred."""
            if (
                not external_runner_enabled()
                or self.active_plan is None
                or self.active_symbol != symbol
                or self.mutex.state != SlotState.ENTRY_PENDING
                or self.active_source_equilibrium_checkpoint is None
            ):
                return False
            instrument_id = instruments[symbol].id
            if not self.portfolio.is_flat(instrument_id):
                return False
            direction = self.active_plan.direction.value
            midpoint = float(self.active_source_equilibrium_checkpoint)
            delivered = (
                float(observation.high) >= midpoint
                if direction == "LONG"
                else float(observation.low) <= midpoint
            )
            if not delivered:
                return False
            if self.cache.orders_open_count(
                instrument_id=instrument_id,
                strategy_id=self.id,
            ):
                self.cancel_all_orders(instrument_id)
            self.lifecycle.append({
                "type": "PENDING_ENTRY_CANCELED_AFTER_EQUILIBRIUM_DELIVERY",
                "ts_event": int(observation.ts_ns),
                "scenario_id": self.active_plan.scenario_id,
                "symbol": symbol,
                "direction": direction,
                "source_equilibrium_checkpoint": midpoint,
                "bar_high": float(observation.high),
                "bar_low": float(observation.low),
                "reason": (
                    "the source-equilibrium first-delivery state completed "
                    "before the passive parent obtained position ownership"
                ),
            })
            return True

        def _maybe_fund_external_runner(
            self,
            symbol: str,
            observation: BarObs,
        ) -> bool:
            """Fund residual raid risk at source equilibrium, then retain runner."""
            if (
                not external_runner_enabled()
                or not funded_equilibrium_runner_enabled()
                or self.external_runner_funded
                or self.active_plan is None
                or self.active_symbol != symbol
                or self.active_cost_record is None
                or self.active_source_equilibrium_checkpoint is None
                or self.mutex.state != SlotState.POSITION_OPEN
                or self.active_plan.scenario.value != "FAR"
            ):
                return False
            instrument = instruments[symbol]
            instrument_id = instrument.id
            if self.portfolio.is_flat(instrument_id):
                return False
            direction = self.active_plan.direction.value
            midpoint = float(self.active_source_equilibrium_checkpoint)
            current = float(observation.close)
            delivered = (
                current >= midpoint if direction == "LONG" else current <= midpoint
            )
            if not delivered:
                return False

            total_quantity = Decimal(
                str(self.active_cost_record.get("entry_filled_qty", 0.0))
            )
            planned_quantity = Decimal(
                str(self.active_cost_record.get("quantity", 0.0))
            )
            expected_total_loss = Decimal(
                str(self.active_cost_record.get("expected_total_loss", 0.0))
            )
            if (
                total_quantity <= 0
                or planned_quantity <= 0
                or expected_total_loss <= 0
            ):
                return False
            reduction = solve_funded_reduction(
                direction=direction,
                total_quantity=total_quantity,
                entry_price=Decimal(str(self.active_plan.expected_entry)),
                current_price=Decimal(str(current)),
                original_loss_per_unit=(
                    expected_total_loss / planned_quantity
                ),
                maker_fee=Decimal(
                    str(execution_config["effective_maker_rate"])
                ),
                taker_fee=Decimal(
                    str(execution_config["effective_taker_rate"])
                ),
                impact_per_side=Decimal(
                    str(self.active_cost_record["impact_per_side"])
                ),
                tick_size=Decimal(str(instrument.price_increment)),
                quantity_increment=Decimal(str(instrument.size_increment)),
                min_quantity=Decimal(str(instrument.min_quantity)),
            )
            if reduction is None:
                return False

            self.external_runner_funded = True
            record = self.active_cost_record
            record["external_runner_funded"] = True
            record["funded_equilibrium_observed_ts_ns"] = int(
                observation.ts_ns
            )
            record["funded_equilibrium_partial_fraction"] = float(
                reduction.fraction
            )
            record["funded_equilibrium_partial_quantity"] = float(
                reduction.partial_quantity
            )
            record["funded_equilibrium_residual_quantity"] = float(
                reduction.residual_quantity
            )
            record["funded_equilibrium_locked_profit"] = float(
                reduction.locked_profit
            )
            record["funded_equilibrium_residual_max_loss"] = float(
                reduction.residual_max_loss
            )

            self.cancel_all_orders(instrument_id)
            target = float(self.active_plan.target_price)
            target_already_delivered = (
                current >= target if direction == "LONG" else current <= target
            )
            if target_already_delivered:
                self.lifecycle.append({
                    "type": "EXTERNAL_RUNNER_TARGET_ALREADY_DELIVERED",
                    "ts_event": int(observation.ts_ns),
                    "scenario_id": self.active_plan.scenario_id,
                    "symbol": symbol,
                    "current": current,
                    "target": target,
                })
                self.close_all_positions(instrument_id)
                return True

            exit_side = (
                OrderSide.SELL if direction == "LONG" else OrderSide.BUY
            )
            try:
                partial_order = self.order_factory.market(
                    instrument_id=instrument_id,
                    order_side=exit_side,
                    quantity=instrument.make_qty(
                        reduction.partial_quantity
                    ),
                    reduce_only=True,
                    tags=["V52_FUNDED_SOURCE_EQUILIBRIUM_REDUCTION"],
                )
                self.replacement_exit_roles[
                    partial_order.client_order_id
                ] = "V52_FUNDED_EQUILIBRIUM_PARTIAL"
                self.submit_order(partial_order)

                stop_order = None
                target_order = None
                if reduction.residual_quantity > 0:
                    residual_quantity = instrument.make_qty(
                        reduction.residual_quantity
                    )
                    stop_order = self.order_factory.stop_market(
                        instrument_id=instrument_id,
                        order_side=exit_side,
                        quantity=residual_quantity,
                        trigger_price=instrument.make_price(
                            self.active_plan.stop_price
                        ),
                        trigger_type=TriggerType.LAST_PRICE,
                        reduce_only=True,
                        tags=["V52_RESIDUAL_ORIGINAL_RAID_INVALIDATION"],
                    )
                    target_order = self.order_factory.limit(
                        instrument_id=instrument_id,
                        order_side=exit_side,
                        quantity=residual_quantity,
                        price=instrument.make_price(target),
                        post_only=True,
                        reduce_only=True,
                        tags=["V52_RESIDUAL_INDEPENDENT_EXTERNAL_DRAW"],
                    )
                    self.replacement_exit_roles[
                        stop_order.client_order_id
                    ] = "V52_RESIDUAL_STOP"
                    self.replacement_exit_roles[
                        target_order.client_order_id
                    ] = "V52_RESIDUAL_TARGET"
                    self._register_replacement_pair(
                        stop_order,
                        target_order,
                    )
                    self.submit_order(stop_order)
                    self.submit_order(target_order)
            except Exception as exc:
                self.errors.append({
                    "type": "V52_FUNDED_RUNNER_SUBMISSION_EXCEPTION",
                    "ts_ns": int(observation.ts_ns),
                    "scenario_id": self.active_plan.scenario_id,
                    "symbol": symbol,
                    "exception": type(exc).__name__,
                    "message": str(exc),
                })
                self.cancel_all_orders(instrument_id)
                if not self.portfolio.is_flat(instrument_id):
                    self.close_all_positions(instrument_id)
                return True

            self.lifecycle.append({
                "type": "FUNDED_SOURCE_EQUILIBRIUM_EXTERNAL_RUNNER_SUBMITTED",
                "ts_event": int(observation.ts_ns),
                "scenario_id": self.active_plan.scenario_id,
                "symbol": symbol,
                "direction": direction,
                "source_equilibrium_checkpoint": midpoint,
                "current": current,
                "external_target": target,
                "partial_fraction": float(reduction.fraction),
                "partial_quantity": float(reduction.partial_quantity),
                "residual_quantity": float(reduction.residual_quantity),
                "locked_profit": float(reduction.locked_profit),
                "residual_max_loss": float(reduction.residual_max_loss),
                "partial_client_order_id": str(
                    partial_order.client_order_id
                ),
                "stop_client_order_id": (
                    None if stop_order is None else str(
                        stop_order.client_order_id
                    )
                ),
                "target_client_order_id": (
                    None if target_order is None else str(
                        target_order.client_order_id
                    )
                ),
            })
            return True

'''
    text = replace_once(
        text,
        "        def _maybe_exit_void_close(\n",
        methods + "        def _maybe_exit_void_close(\n",
        "v52 checkpoint methods",
    )

    text = replace_once(
        text,
        '''                if self._cancel_pending_entry_after_target_delivery(
                    symbol,
                    observation,
                ):
                    continue
''',
        '''                if self._cancel_pending_entry_after_equilibrium_delivery(
                    symbol,
                    observation,
                ):
                    continue
                if self._cancel_pending_entry_after_target_delivery(
                    symbol,
                    observation,
                ):
                    continue
''',
        "v52 pending checkpoint cancellation",
    )
    text = replace_once(
        text,
        '''                if not void_close_failed:
                    self._maybe_transfer_internal_pivot(symbol, observation)
''',
        '''                runner_transitioned = False
                if not void_close_failed:
                    runner_transitioned = self._maybe_fund_external_runner(
                        symbol,
                        observation,
                    )
                if not void_close_failed and not runner_transitioned:
                    self._maybe_transfer_internal_pivot(symbol, observation)
''',
        "v52 funded checkpoint hook",
    )

    reset_old = '''                    self.active_entry_order_id = None
                    self.protection_activation_fail_close_pending = False
                    self.void_close_exit_triggered = False
                    self.internal_pivot_protection_armed = False
'''
    reset_new = '''                    self.active_entry_order_id = None
                    self.protection_activation_fail_close_pending = False
                    self.void_close_exit_triggered = False
                    self.external_runner_funded = False
                    self.active_source_equilibrium_checkpoint = None
                    self.internal_pivot_protection_armed = False
'''
    count = text.count(reset_old)
    if count != 2:
        raise RuntimeError(
            f"v52 terminal reset: expected two markers, found {count}"
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
