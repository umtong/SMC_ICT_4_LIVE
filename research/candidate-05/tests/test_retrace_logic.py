from __future__ import annotations

import unittest

from retrace_logic import aggregate_completed_bar
from retrace_logic import displacement_retrace_level
from retrace_logic import pending_limit_invalidated
from retrace_logic import structural_stop


class RetraceLogicTest(unittest.TestCase):
    def test_midpoint_and_stop_are_mirror_symmetric(self) -> None:
        self.assertEqual(displacement_retrace_level(105.0, 99.0), 102.0)
        self.assertEqual(displacement_retrace_level(95.0, 101.0), 98.0)
        self.assertEqual(structural_stop(105.0, -1, 2.0, 0.1), 105.2)
        self.assertEqual(structural_stop(95.0, 1, 2.0, 0.1), 94.8)

    def test_pending_limit_invalidation_is_directional(self) -> None:
        self.assertTrue(pending_limit_invalidated(side=1, stop=94.8, high=100.0, low=94.7))
        self.assertFalse(pending_limit_invalidated(side=1, stop=94.8, high=100.0, low=94.9))
        self.assertTrue(pending_limit_invalidated(side=-1, stop=105.2, high=105.3, low=100.0))
        self.assertFalse(pending_limit_invalidated(side=-1, stop=105.2, high=105.1, low=100.0))

    def test_completed_five_minute_aggregation_uses_only_given_rows(self) -> None:
        rows = [
            {
                "ts": index,
                "open": 100.0 + index,
                "high": 102.0 + index,
                "low": 99.0 - index,
                "close": 101.0 + index,
                "volume": 10.0,
            }
            for index in range(5)
        ]
        bar = aggregate_completed_bar(rows)
        self.assertEqual(bar["ts"], 4)
        self.assertEqual(bar["open"], 100.0)
        self.assertEqual(bar["high"], 106.0)
        self.assertEqual(bar["low"], 95.0)
        self.assertEqual(bar["close"], 105.0)
        self.assertEqual(bar["volume"], 50.0)


if __name__ == "__main__":
    unittest.main()
