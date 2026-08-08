#!/usr/bin/env python3
"""Patch v44 target hierarchy with the v45 entry-leg invalidation."""
from __future__ import annotations

import argparse
from pathlib import Path

from c10_v44_patch import patch as patch_v44


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected one marker, found {count}")
    return text.replace(old, new, 1)


def patch(path: Path) -> None:
    patch_v44(path)
    text = path.read_text(encoding="utf-8")
    text = replace_once(
        text,
        "from c10_v44_overlay import (\n",
        "from c10_v45_overlay import (\n"
        "    reframe_entry_leg_invalidation,\n",
        "v45 overlay import",
    )

    text = replace_once(
        text,
        '''                    else:
                        plan = timing.plan
                if plan is not None:
                    target = reframe_primary_target(
''',
        '''                    else:
                        plan = timing.plan
                if plan is not None:
                    invalidation = reframe_entry_leg_invalidation(
                        plan,
                        self.logic[symbol],
                    )
                    if not invalidation.approved:
                        self.logic[symbol].mark_rejected(
                            plan,
                            ts_ns,
                            invalidation.reason,
                            invalidation.details,
                        )
                        self._capture_events(symbol)
                        self.rejections.append({
                            "type": "SOURCE_ENTRY_LEG_INVALIDATION_REJECTED",
                            "observed_ts_ns": plan.observed_ts_ns,
                            "scenario_id": plan.scenario_id,
                            "symbol": symbol,
                            "reason": invalidation.reason,
                            "details": invalidation.details,
                            "net_structural_r": str(plan.net_r),
                        })
                        plan = None
                    else:
                        plan = invalidation.plan
                if plan is not None:
                    target = reframe_primary_target(
''',
        "v45 invalidation before target hierarchy",
    )

    method = '''        def _cancel_pending_entry_after_invalidation(
            self,
            symbol: str,
            observation: BarObs,
        ) -> bool:
            """Cancel a passive parent when its invalidation trades first."""
            if (
                self.active_plan is None
                or self.active_symbol != symbol
                or self.mutex.state != SlotState.ENTRY_PENDING
            ):
                return False
            instrument_id = instruments[symbol].id
            if not self.portfolio.is_flat(instrument_id):
                return False
            direction = self.active_plan.direction.value
            stop = float(self.active_plan.stop_price)
            invalidated = (
                float(observation.low) <= stop
                if direction == "LONG"
                else float(observation.high) >= stop
            )
            if not invalidated:
                return False
            if self.cache.orders_open_count(
                instrument_id=instrument_id,
                strategy_id=self.id,
            ):
                self.cancel_all_orders(instrument_id)
            self.lifecycle.append({
                "type": "PENDING_ENTRY_CANCELED_AFTER_INVALIDATION",
                "ts_event": int(observation.ts_ns),
                "scenario_id": self.active_plan.scenario_id,
                "symbol": symbol,
                "direction": direction,
                "stop": stop,
                "bar_high": float(observation.high),
                "bar_low": float(observation.low),
                "reason": (
                    "the active invalidation traded before the passive parent "
                    "obtained position ownership"
                ),
            })
            return True

'''
    text = replace_once(
        text,
        "        def _cancel_pending_entry_after_target_delivery(\n",
        method + "        def _cancel_pending_entry_after_target_delivery(\n",
        "v45 pending-invalidation method",
    )
    text = replace_once(
        text,
        '''                if self._cancel_pending_entry_after_target_delivery(
                    symbol,
                    observation,
                ):
                    continue
''',
        '''                if self._cancel_pending_entry_after_invalidation(
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
        "v45 pending-invalidation hook",
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
