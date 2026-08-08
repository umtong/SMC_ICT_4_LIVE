#!/usr/bin/env python3
"""Patch the frozen Candidate 11 runner with v64 intraday delivery."""
from __future__ import annotations

import argparse
from pathlib import Path

from c10_v27_patch import patch as patch_v27


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected one marker, found {count}")
    return text.replace(old, new, 1)


def patch(path: Path) -> None:
    patch_v27(path)
    text = path.read_text(encoding="utf-8")

    # Older Binance archives can contain a textual header row in only some
    # daily files. Normalize before concatenated sorting; this changes no valid
    # numeric timestamp and repairs only the mixed-type implementation failure.
    text = replace_once(
        text,
        '''    raw = pd.concat(frames, ignore_index=True)
    raw = raw.drop_duplicates(subset=["open_time"], keep="last").sort_values("open_time", kind="stable")
    first = int(pd.to_numeric(raw["open_time"], errors="raise").iloc[0])
''',
        '''    raw = pd.concat(frames, ignore_index=True)
    raw["open_time"] = pd.to_numeric(raw["open_time"], errors="coerce")
    raw = raw[raw["open_time"].notna()].copy()
    raw["open_time"] = raw["open_time"].astype("int64")
    raw = raw.drop_duplicates(subset=["open_time"], keep="last").sort_values("open_time", kind="stable")
    first = int(raw["open_time"].iloc[0])
''',
        "mixed archive timestamp normalization",
    )
    text = replace_once(
        text,
        "from c10_v27_overlay import (\n",
        "from c10_v64_overlay import (\n"
        "    resolve_intraday_acceptance,\n",
        "v64 execution overlay import",
    )
    text = replace_once(
        text,
        "from session_engine import RegionalHandoffAuctionEngine\n",
        "from c10_v64_intraday_delivery import (\n"
        "    IntradayDeliveryContinuationEngine as RegionalHandoffAuctionEngine,\n"
        ")\n",
        "v64 logic-engine import",
    )
    text = replace_once(
        text,
        "            self.active_entry_order_id: str | None = None\n",
        "            self.active_entry_order_id: str | None = None\n"
        "            self.position_expiry_requested_for: str | None = None\n"
        "            self.protection_fail_close_requested_for: str | None = None\n",
        "position and protection fail-close state",
    )
    text = replace_once(
        text,
        "            self.active_entry_order_id = entry_order_id\n",
        "            self.active_entry_order_id = entry_order_id\n"
        "            self.position_expiry_requested_for = None\n"
        "            self.protection_fail_close_requested_for = None\n",
        "position and protection fail-close reset on submit",
    )
    text = replace_once(
        text,
        '''                plan.details["market_leadership"] = leadership.to_dict()
                if not leadership.approved:
''',
        '''                plan.details["market_leadership"] = leadership.to_dict()
                acceptance = resolve_intraday_acceptance(plan)
                plan.details["intraday_acceptance_router"] = acceptance.details
                if not acceptance.approved:
                    self.logic[symbol].mark_rejected(
                        plan,
                        ts_ns,
                        acceptance.reason,
                        acceptance.details,
                    )
                    self._capture_events(symbol)
                    self.rejections.append({
                        "type": "INTRADAY_ACCEPTANCE_REJECTED",
                        "observed_ts_ns": plan.observed_ts_ns,
                        "scenario_id": plan.scenario_id,
                        "symbol": symbol,
                        "direction": plan.direction.value,
                        "reason": acceptance.reason,
                        "state": acceptance.state,
                        "event_direction_rank": (
                            acceptance.event_direction_rank
                        ),
                        "net_structural_r": str(plan.net_r),
                    })
                    continue
                if not leadership.approved:
''',
        "v64 acceptance router",
    )
    text = replace_once(
        text,
        '''        def on_bar(self, bar: Bar) -> None:
            self.last_ts_ns = int(bar.ts_event)
            self._release_if_terminal(self.last_ts_ns, "BAR_TERMINAL_SYNC")
''',
        '''        def _expire_intraday_position(self, ts_ns: int) -> None:
            if (
                self.active_plan is None
                or self.active_symbol is None
                or self.mutex.state != SlotState.POSITION_OPEN
            ):
                return
            raw_expiry = self.active_plan.details.get("position_expire_ts_ns")
            try:
                expiry_ts_ns = int(raw_expiry)
            except (TypeError, ValueError):
                return
            scenario_id = self.active_plan.scenario_id
            if (
                expiry_ts_ns <= 0
                or ts_ns < expiry_ts_ns
                or self.position_expiry_requested_for == scenario_id
            ):
                return
            instrument_id = instruments[self.active_symbol].id
            if self.portfolio.is_flat(instrument_id):
                return
            self.position_expiry_requested_for = scenario_id
            if self.cache.orders_open_count(
                instrument_id=instrument_id,
                strategy_id=self.id,
            ):
                self.cancel_all_orders(instrument_id)
            self.close_all_positions(instrument_id)
            self.lifecycle.append({
                "type": "INTRADAY_CONTEXT_EXPIRY_REQUESTED",
                "ts_event": ts_ns,
                "scenario_id": scenario_id,
                "symbol": self.active_symbol,
                "position_expire_ts_ns": expiry_ts_ns,
            })

        def on_bar(self, bar: Bar) -> None:
            self.last_ts_ns = int(bar.ts_event)
            self._release_if_terminal(self.last_ts_ns, "BAR_TERMINAL_SYNC")
            self._expire_intraday_position(self.last_ts_ns)
''',
        "intraday position expiry",
    )
    text = replace_once(
        text,
        '''        def on_order_filled(self, event: OrderEvent) -> None:
''',
        '''        def _fail_close_rejected_order(self, event: OrderEvent) -> None:
            """Never leave a pending or open trade after bracket rejection.

            A contingent stop can become marketable in the same one-minute bar
            that fills its passive parent. Nautilus correctly refuses such a
            stale trigger instead of inventing an intrabar sequence. Production
            safety requires canceling every residual order and immediately
            flattening any resulting position. The rejection remains an engine
            error, so the run cannot be promoted as valid alpha evidence.
            """
            if self.active_plan is None or self.active_symbol is None:
                return
            scenario_id = self.active_plan.scenario_id
            if self.protection_fail_close_requested_for == scenario_id:
                return
            instrument_id = instruments[self.active_symbol].id
            self.protection_fail_close_requested_for = scenario_id
            ts_ns = int(event.ts_event)
            had_position = not self.portfolio.is_flat(instrument_id)
            if self.cache.orders_open_count(
                instrument_id=instrument_id,
                strategy_id=self.id,
            ):
                self.cancel_all_orders(instrument_id)
            if had_position:
                self.close_all_positions(instrument_id)
            self.lifecycle.append({
                "type": (
                    "PROTECTIVE_ORDER_REJECTED_FAIL_CLOSED"
                    if had_position
                    else "ENTRY_ORDER_LIST_REJECTED_FAIL_CLOSED"
                ),
                "ts_event": ts_ns,
                "scenario_id": scenario_id,
                "symbol": self.active_symbol,
                "rejected_client_order_id": str(event.client_order_id),
                "had_position": had_position,
            })

        def on_order_filled(self, event: OrderEvent) -> None:
''',
        "rejected-order fail-close helper",
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
            self._release_if_terminal(int(event.ts_event), "ORDER_REJECTED")
            self._fail_close_rejected_order(event)
''',
        "rejected-order fail-close invocation",
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
