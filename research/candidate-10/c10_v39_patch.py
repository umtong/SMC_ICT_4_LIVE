#!/usr/bin/env python3
"""Patch frozen Candidate 11 with v39 entry-auction acceptance."""
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
        "from c10_v39_overlay import (\n"
        "    entry_auction_acceptance_enabled,\n"
        "    evaluate_entry_auction,\n",
        "v39 overlay import",
    )
    text = replace_once(
        text,
        "from c10_v38_state import ConfirmedMicroPivotProtectionEngine as RegionalHandoffAuctionEngine\n",
        "from c10_v39_state import EntryAuctionAcceptanceEngine as RegionalHandoffAuctionEngine\n",
        "v39 state-engine import",
    )
    text = replace_once(
        text,
        "            self.internal_pivot_protection_armed = False\n"
        "            self.replacement_exit_mates: dict[Any, Any] = {}\n",
        "            self.internal_pivot_protection_armed = False\n"
        "            self.entry_auction_evaluated = False\n"
        "            self.entry_auction_accepted: bool | None = None\n"
        "            self.replacement_exit_mates: dict[Any, Any] = {}\n",
        "v39 lifecycle state",
    )
    text = replace_once(
        text,
        '''            self.internal_pivot_protection_armed = False
            self.replacement_exit_mates.clear()
''',
        '''            self.internal_pivot_protection_armed = False
            self.entry_auction_evaluated = False
            self.entry_auction_accepted = None
            self.replacement_exit_mates.clear()
''',
        "v39 submitted-state reset",
    )
    text = replace_once(
        text,
        '''                "micro_pivot_protection_enabled": (
                    micro_pivot_protection_enabled()
                ),
                "internal_pivot_protection_armed": False,
''',
        '''                "micro_pivot_protection_enabled": (
                    micro_pivot_protection_enabled()
                ),
                "entry_auction_acceptance_enabled": (
                    entry_auction_acceptance_enabled()
                ),
                "entry_auction_evaluated": False,
                "entry_auction_accepted": None,
                "entry_auction_fill_ts_ns": 0,
                "entry_auction_observed_ts_ns": 0,
                "entry_auction_boundary": None,
                "entry_auction_completed_close": None,
                "entry_auction_distance_from_boundary": None,
                "internal_pivot_protection_armed": False,
''',
        "v39 cost-record fields",
    )

    method = '''        def _maybe_evaluate_entry_auction(
            self,
            symbol: str,
            observation: BarObs,
        ) -> None:
            if (
                not entry_auction_acceptance_enabled()
                or self.entry_auction_evaluated
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
            fill_ts_ns = int(
                self.active_cost_record.get("first_entry_fill_ts_ns", 0),
            )
            if (
                filled_qty <= 0.0
                or fill_ts_ns <= 0
                or observation.ts_ns < fill_ts_ns
            ):
                return

            boundary = float(self.active_plan.expected_entry)
            decision = evaluate_entry_auction(
                direction=self.active_plan.direction.value,
                completed_close=float(observation.close),
                entry_boundary=boundary,
            )
            self.entry_auction_evaluated = True
            self.entry_auction_accepted = decision.accepted
            self.active_cost_record["entry_auction_evaluated"] = True
            self.active_cost_record["entry_auction_accepted"] = (
                decision.accepted
            )
            self.active_cost_record["entry_auction_fill_ts_ns"] = fill_ts_ns
            self.active_cost_record["entry_auction_observed_ts_ns"] = int(
                observation.ts_ns,
            )
            self.active_cost_record["entry_auction_boundary"] = boundary
            self.active_cost_record["entry_auction_completed_close"] = (
                decision.completed_close
            )
            self.active_cost_record["entry_auction_distance_from_boundary"] = (
                decision.distance_from_boundary
            )

            self.logic[symbol].mark_entry_auction_evaluated(
                fill_ts_ns=fill_ts_ns,
                observed_ts_ns=int(observation.ts_ns),
                direction=decision.direction,
                boundary=decision.boundary,
                completed_close=decision.completed_close,
                accepted=decision.accepted,
                distance_from_boundary=decision.distance_from_boundary,
            )
            self._capture_events(symbol)
            self.lifecycle.append({
                "type": (
                    "ENTRY_AUCTION_ACCEPTED"
                    if decision.accepted
                    else "ENTRY_AUCTION_FAILED_EXIT_SUBMITTED"
                ),
                "ts_event": int(observation.ts_ns),
                "fill_ts_ns": fill_ts_ns,
                "scenario_id": self.active_plan.scenario_id,
                "symbol": symbol,
                "direction": decision.direction,
                "entry_boundary": decision.boundary,
                "completed_close": decision.completed_close,
                "distance_from_boundary": decision.distance_from_boundary,
                "filled_quantity": filled_qty,
            })
            if decision.accepted:
                return

            instrument_id = instruments[symbol].id
            if self.cache.orders_open_count(
                instrument_id=instrument_id,
                strategy_id=self.id,
            ):
                self.cancel_all_orders(instrument_id)
            if not self.portfolio.is_flat(instrument_id):
                self.close_all_positions(instrument_id)

'''
    text = replace_once(
        text,
        "        def _maybe_transfer_internal_pivot(\n",
        method + "        def _maybe_transfer_internal_pivot(\n",
        "v39 entry-auction method",
    )
    text = replace_once(
        text,
        '''                plan = self.logic[symbol].on_bar(observation)
                self._capture_events(symbol)
                self._maybe_transfer_internal_pivot(symbol, observation)
                if plan is None:
''',
        '''                plan = self.logic[symbol].on_bar(observation)
                self._capture_events(symbol)
                self._maybe_evaluate_entry_auction(symbol, observation)
                if not (
                    entry_auction_acceptance_enabled()
                    and self.entry_auction_evaluated
                    and self.entry_auction_accepted is False
                ):
                    self._maybe_transfer_internal_pivot(symbol, observation)
                if plan is None:
''',
        "v39 synchronized evaluation hook",
    )

    reset_old = '''                    self.internal_pivot_protection_armed = False
                    self.replacement_exit_mates.clear()
'''
    reset_new = '''                    self.internal_pivot_protection_armed = False
                    self.entry_auction_evaluated = False
                    self.entry_auction_accepted = None
                    self.replacement_exit_mates.clear()
'''
    count = text.count(reset_old)
    if count != 2:
        raise RuntimeError(
            f"v39 terminal reset: expected two markers, found {count}",
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
