from __future__ import annotations

import unittest

import numpy as np
import pandas as pd

from diagnose_local_15s_mss_fvg import (
    aggregate_complete_clock_bars,
    events_from_detector,
    local_structure_logic,
)


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
    def test_only_completed_accepted_event_is_bridged(self) -> None:
        detector = {
            "scenarios": [
                {
                    "scenario_id": "accepted-1",
                    "outcome": "EVENT_ACCEPTED",
                    "direction": "SHORT",
                    "pool_id": "5MH-source",
                    "liquidity_level": 100.0,
                    "event_extreme": 101.0,
                    "contact": {"timestamp_ns": 10},
                    "recovery_terminal": {"timestamp_ns": 20, "close": 99.0},
                },
                {
                    "scenario_id": "rejected-1",
                    "outcome": "REJECT_IMPACT_PER_FLOW",
                },
            ]
        }
        events, details = events_from_detector(detector)
        self.assertEqual(len(events), 1)
        event = events[0]
        self.assertEqual(event.event_end_ns, 20)
        self.assertEqual(event.source_pool_id, "5MH-source")
        source = details[event.event_id]
        self.assertEqual(source["mss_search_begins_after_ns"], 20)
        for forbidden in ("entry", "target", "position", "pnl", "nav"):
            self.assertNotIn(forbidden, source)


if __name__ == "__main__":
    unittest.main()
