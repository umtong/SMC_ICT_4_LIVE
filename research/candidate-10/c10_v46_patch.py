#!/usr/bin/env python3
"""Patch v45 frozen infrastructure with v46 completed-close failure exit."""
from __future__ import annotations

import argparse
from pathlib import Path

from c10_v45_patch import patch as patch_v45


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected one marker, found {count}")
    return text.replace(old, new, 1)


def patch(path: Path) -> None:
    patch_v45(path)
    text = path.read_text(encoding="utf-8")
    text = replace_once(
        text,
        "from c10_v45_overlay import (\n",
        "from c10_v46_overlay import (\n"
        "    evaluate_void_close,\n"
        "    void_close_exit_enabled,\n",
        "v46 overlay import",
    )

    text = replace_once(
        text,
        "            self.active_entry_order_id: str | None = None\n"
        "            self.protection_activation_fail_close_pending = False\n",
        "            self.active_entry_order_id: str | None = None\n"
        "            self.protection_activation_fail_close_pending = False\n"
        "            self.void_close_exit_triggered = False\n",
        "v46 lifecycle state",
    )
    text = replace_once(
        text,
        '''            self.active_entry_order_id = entry_order_id
            self.protection_activation_fail_close_pending = False
            self.internal_pivot_protection_armed = False
''',
        '''            self.active_entry_order_id = entry_order_id
            self.protection_activation_fail_close_pending = False
            self.void_close_exit_triggered = False
            self.internal_pivot_protection_armed = False
''',
        "v46 submitted-state reset",
    )
    text = replace_once(
        text,
        '''                "pivot_reference_contract": None,
                "pivot_reference_level": None,
''',
        '''                "pivot_reference_contract": None,
                "pivot_reference_level": None,
                "void_close_exit_enabled": void_close_exit_enabled(),
                "void_close_exit_triggered": False,
                "void_close_exit_fill_ts_ns": 0,
                "void_close_exit_observed_ts_ns": 0,
                "void_close_exit_boundary": None,
                "void_close_exit_completed_close": None,
                "void_close_exit_signed_distance": None,
''',
        "v46 cost-record fields",
    )

    method = '''        def _maybe_exit_void_close(
            self,
            symbol: str,
            observation: BarObs,
        ) -> bool:
            """Exit when a completed bar closes through the entry-owning void."""
            if (
                not void_close_exit_enabled()
                or self.void_close_exit_triggered
                or self.active_plan is None
                or self.active_symbol != symbol
                or self.active_cost_record is None
                or self.mutex.state != SlotState.POSITION_OPEN
                or self.active_plan.scenario.value != "FAR"
            ):
                return False
            instrument_id = instruments[symbol].id
            if self.portfolio.is_flat(instrument_id):
                return False
            filled_qty = float(
                self.active_cost_record.get("entry_filled_qty", 0.0),
            )
            fill_ts_ns = int(
                self.active_cost_record.get("first_entry_fill_ts_ns", 0),
            )
            if (
                filled_qty <= 0.0
                or fill_ts_ns <= 0
                or observation.ts_ns < fill_ts_ns
            ):
                return False
            zone_low_raw = self.active_plan.details.get("zone_low")
            zone_high_raw = self.active_plan.details.get("zone_high")
            if zone_low_raw is None or zone_high_raw is None:
                self.errors.append({
                    "type": "V46_FIRST_DISPLACEMENT_VOID_UNAVAILABLE",
                    "ts_ns": int(observation.ts_ns),
                    "scenario_id": self.active_plan.scenario_id,
                    "symbol": symbol,
                })
                self.void_close_exit_triggered = True
                self.cancel_all_orders(instrument_id)
                self.close_all_positions(instrument_id)
                return True
            decision = evaluate_void_close(
                direction=self.active_plan.direction.value,
                completed_close=float(observation.close),
                zone_low=float(zone_low_raw),
                zone_high=float(zone_high_raw),
            )
            if not decision.failed:
                return False

            self.void_close_exit_triggered = True
            self.active_cost_record["void_close_exit_triggered"] = True
            self.active_cost_record["void_close_exit_fill_ts_ns"] = fill_ts_ns
            self.active_cost_record["void_close_exit_observed_ts_ns"] = int(
                observation.ts_ns,
            )
            self.active_cost_record["void_close_exit_boundary"] = (
                decision.boundary
            )
            self.active_cost_record["void_close_exit_completed_close"] = (
                decision.completed_close
            )
            self.active_cost_record["void_close_exit_signed_distance"] = (
                decision.signed_distance_from_boundary
            )
            self.lifecycle.append({
                "type": "FIRST_DISPLACEMENT_VOID_CLOSE_FAILURE_EXIT_SUBMITTED",
                "ts_event": int(observation.ts_ns),
                "fill_ts_ns": fill_ts_ns,
                "scenario_id": self.active_plan.scenario_id,
                "symbol": symbol,
                "direction": decision.direction,
                "void_boundary": decision.boundary,
                "completed_close": decision.completed_close,
                "signed_distance_from_boundary": (
                    decision.signed_distance_from_boundary
                ),
                "hard_source_raid_stop": float(
                    self.active_plan.stop_price,
                ),
                "target": float(self.active_plan.target_price),
                "filled_quantity": filled_qty,
                "reason": (
                    "completed one-minute close invalidated the first "
                    "displacement leg while the original source-raid stop "
                    "remained the catastrophe protection and sizing basis"
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
        "        def _enforce_protection_fail_close_after_fill(\n",
        method + "        def _enforce_protection_fail_close_after_fill(\n",
        "v46 void-close method",
    )
    text = replace_once(
        text,
        '''                plan = self.logic[symbol].on_bar(observation)
                self._capture_events(symbol)
                if plan is not None:
''',
        '''                plan = self.logic[symbol].on_bar(observation)
                self._capture_events(symbol)
                void_close_failed = self._maybe_exit_void_close(
                    symbol,
                    observation,
                )
                if plan is not None:
''',
        "v46 synchronized failure hook",
    )
    text = replace_once(
        text,
        '''                self._maybe_transfer_internal_pivot(symbol, observation)
                if plan is None:
''',
        '''                if not void_close_failed:
                    self._maybe_transfer_internal_pivot(symbol, observation)
                if plan is None:
''',
        "v46 management exclusion",
    )

    reset_old = '''                    self.active_entry_order_id = None
                    self.protection_activation_fail_close_pending = False
                    self.internal_pivot_protection_armed = False
'''
    reset_new = '''                    self.active_entry_order_id = None
                    self.protection_activation_fail_close_pending = False
                    self.void_close_exit_triggered = False
                    self.internal_pivot_protection_armed = False
'''
    count = text.count(reset_old)
    if count != 2:
        raise RuntimeError(
            f"v46 terminal reset: expected two markers, found {count}",
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
