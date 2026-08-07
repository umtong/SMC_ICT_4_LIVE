from __future__ import annotations

import unittest

import numpy as np
import pandas as pd

from diagnose_local_15s_mss_fvg import (
    aggregate_complete_clock_bars,
    diagnose_local_mss_fvg,
    events_from_detector,
    local_structure_logic,
)
from model_impact_mss_fvg import ImpactEvent, ImpactMSSFVGLogic


NS = 1_000_000_000


class CompleteClockAggregationTests(unittest.TestCase):
    def _seconds(self, count: int = 31) -> pd.DataFrame:
        timestamps = np.array(
            [index * NS + NS - 1 for index in range(count)],
            dtype=np.int64,
        )
        close = np.arange(count, dtype=float) + 100.0
        return pd.DataFrame(
            {
                "timestamp_ns": timestamps,
                "open": close - 0.25,
                "high": close + 0.50,
                "low": close - 0.50,
                "close": close,
                "atr": np.arange(count, dtype=float) + 1.0,
            }
        )

    def test_only_complete_unix_aligned_bars_are_retained(self) -> None:
        bars = aggregate_complete_clock_bars(self._seconds(), bar_seconds=15)
        self.assertEqual(len(bars.index), 2)
        self.assertEqual(bars["source_seconds"].tolist(), [15, 15])
        self.assertEqual(int(bars.iloc[0]["timestamp_ns"]), 15 * NS - 1)
        self.assertEqual(float(bars.iloc[0]["open"]), 99.75)
        self.assertEqual(float(bars.iloc[0]["high"]), 114.50)
        self.assertEqual(float(bars.iloc[0]["low"]), 99.50)
        self.assertEqual(float(bars.iloc[0]["close"]), 114.0)
        self.assertEqual(float(bars.iloc[0]["atr"]), 15.0)
        self.assertEqual(int(bars.iloc[1]["timestamp_ns"]), 30 * NS - 1)

    def test_clock_gap_is_not_silently_aggregated(self) -> None:
        seconds = self._seconds().drop(index=10).reset_index(drop=True)
        with self.assertRaisesRegex(ValueError, "clock must be contiguous"):
            aggregate_complete_clock_bars(seconds, bar_seconds=15)


class LocalPhysicalTimeLogicTests(unittest.TestCase):
    def test_fifteen_second_clock_preserves_physical_windows(self) -> None:
        logic = local_structure_logic(bar_seconds=15)
        self.assertEqual(logic.pivot_radius, 2)
        self.assertEqual(logic.displacement_rank_period, 240)
        self.assertEqual(logic.maximum_mss_minutes, 20)
        self.assertEqual(logic.maximum_retest_minutes, 20)

    def test_unsupported_clock_cannot_fractionally_rescale_windows(self) -> None:
        with self.assertRaisesRegex(ValueError, "exactly divide"):
            local_structure_logic(bar_seconds=17)


class TargetFreeDetectorBridgeTests(unittest.TestCase):
    def _detector(self) -> dict:
        return {
            "scenarios": [
                {
                    "scenario_id": "accepted-1",
                    "outcome": "EVENT_ACCEPTED",
                    "direction": "SHORT",
                    "pool_id": "5MH-source",
                    "liquidity_level": 100.0,
                    "event_extreme": 101.0,
                    "contact": {"timestamp_ns": 18 * NS - 1},
                    "recovery_terminal": {
                        "timestamp_ns": 20 * NS - 1,
                        "close": 99.0,
                    },
                },
                {
                    "scenario_id": "rejected-1",
                    "outcome": "REJECT_IMPACT_PER_FLOW",
                },
            ]
        }

    def test_only_completed_accepted_event_is_bridged(self) -> None:
        events, details = events_from_detector(
            self._detector(),
            bar_seconds=15,
        )
        self.assertEqual(len(events), 1)
        event = events[0]
        self.assertEqual(event.event_end_ns, 30 * NS - 1)
        self.assertEqual(event.source_pool_id, "5MH-source")
        source = details[event.event_id]
        self.assertEqual(source["detector_recovery_terminal_ns"], 20 * NS - 1)
        self.assertEqual(source["local_search_anchor_ns"], 30 * NS - 1)
        self.assertFalse(source["partial_recovery_bucket_used_for_structure"])
        for forbidden in ("entry", "target", "position", "pnl", "nav"):
            self.assertNotIn(forbidden, source)


class PostEventFVGTests(unittest.TestCase):
    def test_fvg_using_pre_event_source_bars_cannot_route(self) -> None:
        count = 20
        timestamps = np.array(
            [(index + 1) * 15 * NS - 1 for index in range(count)],
            dtype=np.int64,
        )
        close = np.full(count, 99.0)
        open_ = close - 0.02
        high = np.full(count, 99.2)
        low = np.full(count, 98.8)

        # Causal upper swing at index 7, confirmed at index 9.
        high[7] = 100.0
        high[5:7] = [99.3, 99.4]
        high[8:10] = [99.5, 99.6]

        # Event anchor is index 10. Index 11 is a valid displacement and would
        # form a bullish FVG only by using index 9, a pre-event source bar.
        open_[11], close[11], high[11], low[11] = 99.5, 100.6, 100.7, 99.7
        local = pd.DataFrame(
            {
                "timestamp_ns": timestamps,
                "open": open_,
                "high": high,
                "low": low,
                "close": close,
                "atr": np.ones(count),
            }
        )
        event = ImpactEvent(
            event_id="event-1",
            direction="LONG",
            event_end_ns=int(timestamps[10]),
            source_pool_id="5ML-source",
            source_level=98.5,
            event_extreme=95.0,
        )
        logic = ImpactMSSFVGLogic(
            displacement_rank_period=5,
            maximum_mss_minutes=5,
            maximum_retest_minutes=5,
        )
        plans, diagnostics = diagnose_local_mss_fvg(
            local,
            events=[event],
            logic=logic,
            require_fvg_retest=False,
        )
        self.assertEqual(plans, [])
        final = diagnostics[-1]
        self.assertEqual(final.outcome, "MSS_FVG_NOT_CONFIRMED_WITHIN_WINDOW")
        self.assertTrue(final.details["post_event_fvg_required"])


if __name__ == "__main__":
    unittest.main()
