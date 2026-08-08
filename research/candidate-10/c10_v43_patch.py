#!/usr/bin/env python3
"""Patch v41 best entry with v43 funded microstructure risk transfer."""
from __future__ import annotations

import argparse
from pathlib import Path

from c10_v41_patch import patch as patch_v41


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected one marker, found {count}")
    return text.replace(old, new, 1)


def patch(path: Path) -> None:
    patch_v41(path)
    text = path.read_text(encoding="utf-8")

    text = replace_once(
        text,
        "from c10_v41_overlay import (\n",
        "from c10_v43_overlay import (\n"
        "    first_favorable_pivot_observation,\n"
        "    funded_micro_reduction_enabled,\n"
        "    solve_funded_reduction,\n",
        "v43 overlay import",
    )
    text = replace_once(
        text,
        "from c10_v40_state import SourceEquilibriumFailedAuctionEngine as RegionalHandoffAuctionEngine\n",
        "from c10_v43_state import FundedMicroRiskTransferEngine as RegionalHandoffAuctionEngine\n",
        "v43 state-engine import",
    )
    text = replace_once(
        text,
        '''                "internal_pivot_protective_stop": None,
            }
''',
        '''                "internal_pivot_protective_stop": None,
                "funded_micro_reduction_enabled": (
                    funded_micro_reduction_enabled()
                ),
                "funded_micro_reduction_armed": False,
                "funded_micro_pivot_event_ts_ns": 0,
                "funded_micro_pivot_known_ts_ns": 0,
                "funded_micro_pivot_level": None,
                "funded_micro_observed_ts_ns": 0,
                "funded_micro_current_price": None,
                "funded_partial_fraction": None,
                "funded_partial_quantity": None,
                "funded_residual_quantity": None,
                "funded_expected_exit_price": None,
                "funded_gain_per_unit": None,
                "funded_locked_profit": None,
                "funded_residual_max_loss": None,
            }
''',
        "v43 cost-record fields",
    )

    start_marker = "        def _maybe_transfer_internal_pivot("
    end_marker = "\n        def _process_batch"
    start = text.find(start_marker)
    if start < 0:
        raise RuntimeError("v43 transfer method start marker missing")
    end = text.find(end_marker, start)
    if end < 0:
        raise RuntimeError("v43 transfer method end marker missing")

    method = '''        def _maybe_transfer_internal_pivot(
            self,
            symbol: str,
            observation: BarObs,
        ) -> None:
            if (
                not funded_micro_reduction_enabled()
                or self.internal_pivot_protection_armed
                or self.active_plan is None
                or self.active_symbol != symbol
                or self.active_cost_record is None
                or self.mutex.state != SlotState.POSITION_OPEN
                or self.active_plan.scenario.value != "FAR"
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
            entry_fill_ts_ns = int(
                self.active_cost_record.get("first_entry_fill_ts_ns", 0),
            )
            if (
                total_quantity <= 0
                or planned_quantity <= 0
                or expected_total_loss <= 0
                or entry_fill_ts_ns <= 0
            ):
                return

            logic = self.logic[symbol]
            instrument = instruments[symbol]
            pivot = first_favorable_pivot_observation(
                direction=self.active_plan.direction.value,
                micro_highs=logic.micro_highs,
                micro_lows=logic.micro_lows,
                bars=logic.bars,
                entry_fill_ts_ns=entry_fill_ts_ns,
                observed_ts_ns=int(observation.ts_ns),
                entry_reference=float(self.active_plan.expected_entry),
                current_price=float(observation.close),
                target_price=float(self.active_plan.target_price),
            )
            if pivot is None:
                return

            reduction = solve_funded_reduction(
                direction=pivot.direction,
                total_quantity=total_quantity,
                entry_price=Decimal(str(self.active_plan.expected_entry)),
                current_price=Decimal(str(pivot.current_price)),
                original_loss_per_unit=(
                    expected_total_loss / planned_quantity
                ),
                maker_fee=Decimal(
                    str(execution_config["effective_maker_rate"]),
                ),
                taker_fee=Decimal(
                    str(execution_config["effective_taker_rate"]),
                ),
                impact_per_side=Decimal(
                    str(self.active_cost_record["impact_per_side"]),
                ),
                tick_size=Decimal(str(instrument.price_increment)),
                quantity_increment=Decimal(str(instrument.size_increment)),
                min_quantity=Decimal(str(instrument.min_quantity)),
            )
            if reduction is None:
                return

            self.internal_pivot_protection_armed = True
            record = self.active_cost_record
            record["internal_pivot_protection_armed"] = True
            record["funded_micro_reduction_armed"] = True
            record["funded_micro_pivot_event_ts_ns"] = pivot.pivot_event_ts_ns
            record["funded_micro_pivot_known_ts_ns"] = pivot.pivot_known_ts_ns
            record["funded_micro_pivot_level"] = pivot.pivot_level
            record["funded_micro_observed_ts_ns"] = int(observation.ts_ns)
            record["funded_micro_current_price"] = pivot.current_price
            record["funded_partial_fraction"] = float(reduction.fraction)
            record["funded_partial_quantity"] = float(
                reduction.partial_quantity,
            )
            record["funded_residual_quantity"] = float(
                reduction.residual_quantity,
            )
            record["funded_expected_exit_price"] = float(
                reduction.expected_exit_price,
            )
            record["funded_gain_per_unit"] = float(reduction.gain_per_unit)
            record["funded_locked_profit"] = float(reduction.locked_profit)
            record["funded_residual_max_loss"] = float(
                reduction.residual_max_loss,
            )

            instrument_id = instrument.id
            self.cancel_all_orders(instrument_id)
            exit_side = (
                OrderSide.SELL
                if pivot.direction == "LONG"
                else OrderSide.BUY
            )
            try:
                partial_order = self.order_factory.market(
                    instrument_id=instrument_id,
                    order_side=exit_side,
                    quantity=instrument.make_qty(
                        reduction.partial_quantity,
                    ),
                    reduce_only=True,
                    tags=["V43_FUNDED_MICRO_PARTIAL_REDUCTION"],
                )
                self.replacement_exit_roles[
                    partial_order.client_order_id
                ] = "FUNDED_MICRO_PARTIAL"
                self.submit_order(partial_order)

                stop_order = None
                target_order = None
                if reduction.residual_quantity > 0:
                    residual_quantity = instrument.make_qty(
                        reduction.residual_quantity,
                    )
                    stop_order = self.order_factory.stop_market(
                        instrument_id=instrument_id,
                        order_side=exit_side,
                        quantity=residual_quantity,
                        trigger_price=instrument.make_price(
                            self.active_plan.stop_price,
                        ),
                        trigger_type=TriggerType.LAST_PRICE,
                        reduce_only=True,
                        tags=["V43_RESIDUAL_ORIGINAL_RAID_INVALIDATION"],
                    )
                    target_order = self.order_factory.limit(
                        instrument_id=instrument_id,
                        order_side=exit_side,
                        quantity=residual_quantity,
                        price=instrument.make_price(
                            self.active_plan.target_price,
                        ),
                        post_only=True,
                        reduce_only=True,
                        tags=["V43_RESIDUAL_SOURCE_EQUILIBRIUM_TARGET"],
                    )
                    self.replacement_exit_roles[
                        stop_order.client_order_id
                    ] = "FUNDED_RESIDUAL_STOP"
                    self.replacement_exit_roles[
                        target_order.client_order_id
                    ] = "FUNDED_RESIDUAL_TARGET"
                    self._register_replacement_pair(stop_order, target_order)
                    self.submit_order(stop_order)
                    self.submit_order(target_order)
            except Exception as exc:
                self.errors.append({
                    "type": "V43_FUNDED_REPLACEMENT_SUBMISSION_EXCEPTION",
                    "ts_ns": int(observation.ts_ns),
                    "symbol": symbol,
                    "exception": type(exc).__name__,
                    "message": str(exc),
                })
                self.cancel_all_orders(instrument_id)
                if not self.portfolio.is_flat(instrument_id):
                    self.close_all_positions(instrument_id)
                return

            logic.mark_funded_micro_reduction(
                observed_ts_ns=int(observation.ts_ns),
                pivot_event_ts_ns=pivot.pivot_event_ts_ns,
                direction=pivot.direction,
                pivot_level=pivot.pivot_level,
                entry_reference=pivot.entry_reference,
                partial_quantity=float(reduction.partial_quantity),
                residual_quantity=float(reduction.residual_quantity),
                locked_profit=float(reduction.locked_profit),
                residual_max_loss=float(reduction.residual_max_loss),
            )
            self._capture_events(symbol)
            self.lifecycle.append({
                "type": "FUNDED_MICRO_RISK_TRANSFER_SUBMITTED",
                "ts_event": int(observation.ts_ns),
                "scenario_id": self.active_plan.scenario_id,
                "symbol": symbol,
                "direction": pivot.direction,
                "pivot_event_ts_ns": pivot.pivot_event_ts_ns,
                "pivot_known_ts_ns": pivot.pivot_known_ts_ns,
                "pivot_level": pivot.pivot_level,
                "entry_reference": pivot.entry_reference,
                "current_price": pivot.current_price,
                "partial_fraction": float(reduction.fraction),
                "partial_quantity": float(reduction.partial_quantity),
                "residual_quantity": float(reduction.residual_quantity),
                "locked_profit": float(reduction.locked_profit),
                "residual_max_loss": float(reduction.residual_max_loss),
                "partial_client_order_id": str(
                    partial_order.client_order_id,
                ),
                "stop_client_order_id": (
                    None
                    if stop_order is None
                    else str(stop_order.client_order_id)
                ),
                "target_client_order_id": (
                    None
                    if target_order is None
                    else str(target_order.client_order_id)
                ),
            })
'''
    text = text[:start] + method + text[end:]
    path.write_text(text, encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("path", type=Path)
    args = parser.parse_args()
    patch(args.path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
