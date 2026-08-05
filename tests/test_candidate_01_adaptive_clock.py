from __future__ import annotations

from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
CANDIDATE = ROOT / "research" / "candidate-01"
SRC = ROOT / "src"
for item in (CANDIDATE, SRC):
    if str(item) not in sys.path:
        sys.path.insert(0, str(item))

from adaptive_aggtrade_clock import (
    ClockCandidateEvidence,
    choose_cost_resolved_candidate,
)


def evidence(minutes: int, range_bps: float) -> ClockCandidateEvidence:
    return ClockCandidateEvidence(
        calibration_minutes=minutes,
        target_quote_notional=float(minutes) * 1_000_000.0,
        calibration_event_bars=100,
        median_range_bps=range_bps,
        median_duration_seconds=float(minutes) * 60.0,
    )


class AdaptiveClockSelectionTest(unittest.TestCase):
    def test_selects_smallest_preceding_day_scale_that_clears_cost(self) -> None:
        selected, fallback = choose_cost_resolved_candidate(
            (
                evidence(5, 10.0),
                evidence(10, 14.5),
                evidence(20, 22.0),
                evidence(30, 31.0),
            ),
            minimum_range_bps=14.0,
        )
        self.assertEqual(selected.calibration_minutes, 10)
        self.assertFalse(fallback)

    def test_falls_back_to_largest_scale_when_none_clear_cost(self) -> None:
        selected, fallback = choose_cost_resolved_candidate(
            (
                evidence(5, 5.0),
                evidence(10, 8.0),
                evidence(20, 11.0),
                evidence(30, 13.0),
            ),
            minimum_range_bps=14.0,
        )
        self.assertEqual(selected.calibration_minutes, 30)
        self.assertTrue(fallback)

    def test_selection_is_order_independent(self) -> None:
        selected, _ = choose_cost_resolved_candidate(
            (
                evidence(30, 30.0),
                evidence(10, 15.0),
                evidence(5, 9.0),
                evidence(20, 20.0),
            ),
            minimum_range_bps=14.0,
        )
        self.assertEqual(selected.calibration_minutes, 10)


if __name__ == "__main__":
    unittest.main()
