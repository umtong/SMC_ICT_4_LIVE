from __future__ import annotations

import math
import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from quarter_hour_relaunch_logic import evaluate_second_leg_relaunch


class QuarterHourRelaunchLogicTest(unittest.TestCase):
    def _base(self) -> dict[str, float | int]:
        return {
            "side": 1,
            "atr": 100.0,
            "accepted_boundary": 100.0,
            "opening_extreme": 110.0,
            "retest_high": 109.0,
            "retest_low": 102.0,
            "close": 113.0,
            "tail_flow_15s": 0.35,
            "full_flow_60s": 0.25,
            "return_60s_bps": 3.0,
            "efficiency_60s": 0.40,
            "depth_imbalance_1": 0.20,
            "buffer_atr": 0.02,
            "tail_flow_min": 0.10,
            "full_flow_min": 0.10,
            "efficiency_min": 0.20,
            "queue_min": 0.0,
        }

    def test_long_relaunch_confirms(self) -> None:
        decision = evaluate_second_leg_relaunch(**self._base())
        self.assertEqual(decision.state, "CONFIRMED")
        self.assertAlmostEqual(decision.launch_level, 112.0)

    def test_short_relaunch_confirms_symmetrically(self) -> None:
        values = self._base()
        values.update(
            side=-1,
            accepted_boundary=100.0,
            opening_extreme=90.0,
            retest_high=98.0,
            retest_low=91.0,
            close=87.0,
            tail_flow_15s=-0.35,
            full_flow_60s=-0.25,
            return_60s_bps=-3.0,
            depth_imbalance_1=-0.20,
        )
        decision = evaluate_second_leg_relaunch(**values)
        self.assertEqual(decision.state, "CONFIRMED")
        self.assertAlmostEqual(decision.launch_level, 88.0)

    def test_close_through_accepted_boundary_invalidates_before_entry(self) -> None:
        values = self._base()
        values["close"] = 99.0
        decision = evaluate_second_leg_relaunch(**values)
        self.assertEqual(decision.state, "INVALIDATED")
        self.assertEqual(
            decision.reason,
            "ACCEPTED_RANGE_LOST_BEFORE_SECOND_LEG",
        )

    def test_price_without_renewed_flow_waits(self) -> None:
        values = self._base()
        values["full_flow_60s"] = -0.20
        decision = evaluate_second_leg_relaunch(**values)
        self.assertEqual(decision.state, "WAIT")
        self.assertEqual(
            decision.reason,
            "SECOND_LEG_FULL_FLOW_NOT_DIRECTIONAL",
        )

    def test_inefficient_break_is_not_a_relaunch(self) -> None:
        values = self._base()
        values["efficiency_60s"] = 0.05
        decision = evaluate_second_leg_relaunch(**values)
        self.assertEqual(decision.state, "WAIT")
        self.assertEqual(
            decision.reason,
            "SECOND_LEG_PROGRESS_NOT_EFFICIENT",
        )

    def test_non_finite_observation_never_confirms(self) -> None:
        values = self._base()
        values["tail_flow_15s"] = float("nan")
        decision = evaluate_second_leg_relaunch(**values)
        self.assertEqual(decision.state, "WAIT")
        self.assertTrue(math.isnan(decision.launch_level))


if __name__ == "__main__":
    unittest.main()
