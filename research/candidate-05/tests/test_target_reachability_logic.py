from __future__ import annotations

import math
import unittest

from target_reachability_logic import MAX_REMAINING_TO_RECLAIM_MULTIPLE
from target_reachability_logic import measured_move_target_reachability


class TargetReachabilityLogicTest(unittest.TestCase):
    def decide(self, *, side=1, boundary=100.0, confirmation=102.0, target=106.0):
        return measured_move_target_reachability(
            side=side,
            session_boundary=boundary,
            confirmation_close=confirmation,
            target=target,
        )

    def test_exact_one_third_completion_is_inclusive(self) -> None:
        decision = self.decide()
        self.assertTrue(decision.reachable)
        self.assertAlmostEqual(decision.completion_fraction, 1.0 / 3.0)
        self.assertEqual(MAX_REMAINING_TO_RECLAIM_MULTIPLE, 2.0)

    def test_less_than_one_third_completion_is_rejected(self) -> None:
        decision = self.decide(target=106.1)
        self.assertFalse(decision.reachable)
        self.assertEqual(
            decision.reason_code,
            "OPPOSING_LIQUIDITY_TARGET_NOT_REACHABLE_FROM_CONFIRMED_RECLAIM",
        )

    def test_long_and_short_are_mirror_symmetric(self) -> None:
        long = self.decide()
        short = self.decide(
            side=-1,
            boundary=100.0,
            confirmation=98.0,
            target=94.0,
        )
        self.assertEqual(long.reachable, short.reachable)
        self.assertAlmostEqual(
            long.demonstrated_reclaim,
            short.demonstrated_reclaim,
        )
        self.assertAlmostEqual(
            long.remaining_target_distance,
            short.remaining_target_distance,
        )

    def test_wrong_direction_and_nonfinite_geometry_are_invalid(self) -> None:
        wrong = self.decide(confirmation=99.0)
        missing = self.decide(target=math.nan)
        self.assertFalse(wrong.reachable)
        self.assertFalse(missing.reachable)
        self.assertEqual(
            wrong.reason_code,
            "REACHABILITY_DIRECTIONAL_GEOMETRY_INVALID",
        )
        self.assertEqual(
            missing.reason_code,
            "REACHABILITY_GEOMETRY_IS_NOT_FINITE",
        )

    def test_side_and_multiplier_contracts_are_strict(self) -> None:
        with self.assertRaises(ValueError):
            measured_move_target_reachability(
                side=0,
                session_boundary=100.0,
                confirmation_close=102.0,
                target=106.0,
            )
        with self.assertRaises(ValueError):
            measured_move_target_reachability(
                side=1,
                session_boundary=100.0,
                confirmation_close=102.0,
                target=106.0,
                maximum_remaining_to_reclaim_multiple=0.0,
            )


if __name__ == "__main__":
    unittest.main()
