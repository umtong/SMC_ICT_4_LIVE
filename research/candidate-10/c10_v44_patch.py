#!/usr/bin/env python3
"""Patch the v41 best entry with the v44 causal target hierarchy."""
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
        "from c10_v44_overlay import (\n"
        "    reframe_primary_target,\n",
        "v44 overlay import",
    )

    text = replace_once(
        text,
        '''                    else:
                        plan = timing.plan
                self._maybe_transfer_internal_pivot(symbol, observation)
                if plan is None:
''',
        '''                    else:
                        plan = timing.plan
                if plan is not None:
                    target = reframe_primary_target(
                        plan,
                        self.logic[symbol],
                    )
                    if not target.approved:
                        self.logic[symbol].mark_rejected(
                            plan,
                            ts_ns,
                            target.reason,
                            target.details,
                        )
                        self._capture_events(symbol)
                        self.rejections.append({
                            "type": "SOURCE_TARGET_HIERARCHY_REJECTED",
                            "observed_ts_ns": plan.observed_ts_ns,
                            "scenario_id": plan.scenario_id,
                            "symbol": symbol,
                            "reason": target.reason,
                            "details": target.details,
                            "net_structural_r": str(plan.net_r),
                        })
                        plan = None
                    else:
                        plan = target.plan
                self._maybe_transfer_internal_pivot(symbol, observation)
                if plan is None:
''',
        "v44 target hierarchy reframe",
    )

    method = '''        def _cancel_pending_entry_after_target_delivery(
            self,
            symbol: str,
            observation: BarObs,
        ) -> bool:
            """Fail closed when the objective is delivered before a passive fill."""
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
            target = float(self.active_plan.target_price)
            delivered = (
                float(observation.high) >= target
                if direction == "LONG"
                else float(observation.low) <= target
            )
            if not delivered:
                return False
            if self.cache.orders_open_count(
                instrument_id=instrument_id,
                strategy_id=self.id,
            ):
                self.cancel_all_orders(instrument_id)
            self.lifecycle.append({
                "type": "PENDING_ENTRY_CANCELED_AFTER_TARGET_DELIVERY",
                "ts_event": int(observation.ts_ns),
                "scenario_id": self.active_plan.scenario_id,
                "symbol": symbol,
                "direction": direction,
                "target": target,
                "bar_high": float(observation.high),
                "bar_low": float(observation.low),
                "reason": (
                    "primary objective delivered before the passive parent "
                    "obtained position ownership"
                ),
            })
            return True

'''
    text = replace_once(
        text,
        "        def _process_batch(self, ts_ns: int) -> None:\n",
        method + "        def _process_batch(self, ts_ns: int) -> None:\n",
        "v44 pending-target method",
    )
    text = replace_once(
        text,
        '''            for symbol in SYMBOLS:
                observation = self.buffer[symbol]
                plan = self.logic[symbol].on_bar(observation)
''',
        '''            for symbol in SYMBOLS:
                observation = self.buffer[symbol]
                if self._cancel_pending_entry_after_target_delivery(
                    symbol,
                    observation,
                ):
                    continue
                plan = self.logic[symbol].on_bar(observation)
''',
        "v44 pending-target hook",
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
