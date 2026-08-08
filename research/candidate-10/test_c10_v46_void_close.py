from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from c10_v46_overlay import evaluate_void_close
from c10_v46_overlay import void_close_exit_enabled


class V46VoidCloseTest(unittest.TestCase):
    def test_long_requires_completed_close_at_or_below_void_low(self) -> None:
        accepted = evaluate_void_close(
            direction="LONG",
            completed_close=99.1,
            zone_low=99.0,
            zone_high=100.0,
        )
        self.assertFalse(accepted.failed)
        self.assertAlmostEqual(accepted.signed_distance_from_boundary, 0.1)
        failed = evaluate_void_close(
            direction="LONG",
            completed_close=99.0,
            zone_low=99.0,
            zone_high=100.0,
        )
        self.assertTrue(failed.failed)
        self.assertEqual(failed.boundary, 99.0)

    def test_short_requires_completed_close_at_or_above_void_high(self) -> None:
        accepted = evaluate_void_close(
            direction="SHORT",
            completed_close=100.9,
            zone_low=100.0,
            zone_high=101.0,
        )
        self.assertFalse(accepted.failed)
        self.assertAlmostEqual(accepted.signed_distance_from_boundary, 0.1)
        failed = evaluate_void_close(
            direction="SHORT",
            completed_close=101.1,
            zone_low=100.0,
            zone_high=101.0,
        )
        self.assertTrue(failed.failed)
        self.assertAlmostEqual(failed.signed_distance_from_boundary, -0.1)

    def test_invalid_void_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            evaluate_void_close(
                direction="LONG",
                completed_close=100.0,
                zone_low=100.0,
                zone_high=100.0,
            )

    def test_environment_flag_is_exact(self) -> None:
        with patch.dict(os.environ, {"C10_V46_VOID_CLOSE_EXIT": "1"}):
            self.assertTrue(void_close_exit_enabled())
        with patch.dict(os.environ, {"C10_V46_VOID_CLOSE_EXIT": "0"}):
            self.assertFalse(void_close_exit_enabled())


if __name__ == "__main__":
    unittest.main()
