from __future__ import annotations

from pathlib import Path
import sys
import unittest

import pandas as pd

CANDIDATE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(CANDIDATE_DIR))

from diagnose_parent_initiative_ladder import (  # noqa: E402
    AcceptanceEvent,
    parent_state_timeline,
    state_before_timestamp,
)


class ParentInitiativeLadderTests(unittest.TestCase):
    def _bars(self, closes: list[float]) -> pd.DataFrame:
        return pd.DataFrame(
            {
                "timestamp_ns": [
                    (index + 1) * 15 * 60 * 1_000_000_000
                    for index in range(len(closes))
                ],
                "open": closes,
                "high": [value + 1.0 for value in closes],
                "low": [value - 1.0 for value in closes],
                "close": closes,
            }
        )

    def test_two_distinct_upper_acceptances_activate_bullish_parent(self) -> None:
        bars = self._bars([100.0, 102.0, 104.0, 105.0])
        events = [
            AcceptanceEvent(1, int(bars.iloc[1].timestamp_ns), "UPPER", "A", 101.0),
            AcceptanceEvent(2, int(bars.iloc[2].timestamp_ns), "UPPER", "B", 103.0),
        ]
        timeline = parent_state_timeline(bars, events)
        self.assertIsNone(timeline[1])
        self.assertIsNotNone(timeline[2])
        self.assertEqual(timeline[2].direction, "LONG")
        self.assertEqual(timeline[2].anchor_level, 101.0)

    def test_reclaim_of_first_boundary_ends_parent_initiative(self) -> None:
        bars = self._bars([100.0, 102.0, 104.0, 100.5])
        events = [
            AcceptanceEvent(1, int(bars.iloc[1].timestamp_ns), "UPPER", "A", 101.0),
            AcceptanceEvent(2, int(bars.iloc[2].timestamp_ns), "UPPER", "B", 103.0),
        ]
        timeline = parent_state_timeline(bars, events)
        self.assertIsNotNone(timeline[2])
        self.assertIsNone(timeline[3])

    def test_opposite_acceptance_resets_chain(self) -> None:
        bars = self._bars([100.0, 102.0, 99.0, 98.0])
        events = [
            AcceptanceEvent(1, int(bars.iloc[1].timestamp_ns), "UPPER", "A", 101.0),
            AcceptanceEvent(2, int(bars.iloc[2].timestamp_ns), "LOWER", "B", 99.5),
            AcceptanceEvent(3, int(bars.iloc[3].timestamp_ns), "LOWER", "C", 98.5),
        ]
        timeline = parent_state_timeline(bars, events)
        self.assertIsNone(timeline[2])
        self.assertIsNotNone(timeline[3])
        self.assertEqual(timeline[3].direction, "SHORT")

    def test_query_uses_only_parent_bar_completed_before_contact(self) -> None:
        bars = self._bars([100.0, 102.0, 104.0])
        events = [
            AcceptanceEvent(1, int(bars.iloc[1].timestamp_ns), "UPPER", "A", 101.0),
            AcceptanceEvent(2, int(bars.iloc[2].timestamp_ns), "UPPER", "B", 103.0),
        ]
        timeline = parent_state_timeline(bars, events)
        at_same_close = state_before_timestamp(
            bars,
            timeline,
            int(bars.iloc[2].timestamp_ns),
        )
        after_close = state_before_timestamp(
            bars,
            timeline,
            int(bars.iloc[2].timestamp_ns) + 1,
        )
        self.assertIsNone(at_same_close)
        self.assertIsNotNone(after_close)


if __name__ == "__main__":
    unittest.main()
