#!/usr/bin/env python3
"""Candidate 05 v32b: repair only the ArmedEntryPath timestamp contract."""
from __future__ import annotations

from strategy_base import PendingSetup
from strategy_v9 import ArmedEntryPath
from strategy_v32_queue_pressure_release import QueuePressureReleaseStrategy
from strategy_v32_queue_pressure_release import QueueReleaseWatch
from strategy_v32_queue_pressure_release import QueueSnapshot


class QueuePressureReleaseStrategyV2(QueuePressureReleaseStrategy):
    """Preserve v32 market logic and construct the current entry-path contract."""

    def _submit_queue_release(
        self,
        watch: QueueReleaseWatch,
        bar: QueueSnapshot,
    ) -> None:
        entry_price = self.instrument.make_price(watch.entry)
        stop_price = self.instrument.make_price(watch.stop)
        target_price = self.instrument.make_price(watch.target)
        details = {
            **watch.details,
            "implementation_revision": "v32b_armed_entry_path_created_ts_contract",
            "retest_index": self.bar_index,
            "retest_ts": bar.ts,
            "retest_close": bar.close,
            "retest_flow_15s": bar.flow_15s,
            "retest_depth_imbalance": bar.depth_imbalance,
        }
        setup = PendingSetup(
            scenario_id=watch.scenario_id,
            branch=self.BRANCH,
            side=watch.side,
            swept_kind="QUEUE_PRESSURE",
            pool_id=watch.target_pool_id,
            pool_level=watch.boundary,
            created_index=watch.created_index,
            expires_index=watch.expires_index,
            sweep_extreme=(
                watch.compression.upper
                if watch.side > 0
                else watch.compression.lower
            ),
            structure=watch.boundary,
            atr=self._atr(),
            hold_count=0,
            retrace_armed=True,
            details=details,
        )
        armed = ArmedEntryPath(
            setup=setup,
            flow_state="QUEUE_PRESSURE_CONFIRMED_RELEASE",
            choch_close=watch.entry,
            stop=watch.stop,
            atr=self._atr(),
            created_index=watch.created_index,
            created_ts=bar.ts,
            details=details,
        )
        submitted = self._submit_price_capped_bracket(
            armed=armed,
            row={
                "ts": bar.ts,
                "open": bar.open,
                "high": bar.high,
                "low": bar.low,
                "close": bar.close,
            },
            entry_price=entry_price,
            stop_price=stop_price,
            target_price=target_price,
            sizing_entry=watch.entry,
            planned_loss=watch.planned_loss,
            target_source=watch.target_source,
            target_r=watch.target_r,
            branch=self.BRANCH,
            event_type="QUEUE_PRESSURE_BOUNDARY_LIMIT_SUBMITTED",
            reason="FIRST_DEFENDED_RETEST_AFTER_CONFIRMED_QUEUE_PRESSURE_RELEASE",
            expires_index=watch.expires_index,
            entry_tag="QUEUE_PRESSURE_RELEASE_ENTRY",
            extra=details,
        )
        if submitted:
            self.queue_release_watch = None
            self.diagnostics["queue_pressure_submissions"] += 1
        else:
            self._close_queue_watch(
                bar,
                "QUEUE_PRESSURE_BRACKET_SUBMISSION_FAILED",
            )


__all__ = ["QueuePressureReleaseStrategyV2"]
