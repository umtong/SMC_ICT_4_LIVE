from __future__ import annotations

import math
import unittest

from scenario_target_logic import frozen_target_reached_before_entry
from scenario_target_logic import revalidate_frozen_milestone


class ScenarioTargetLogicTest(unittest.TestCase):
    def test_target_completion_is_mirror_symmetric(self) -> None:
        long_reached = frozen_target_reached_before_entry(
            side=1,
            target=105.0,
            high=105.0,
            low=101.0,
        )
        short_reached = frozen_target_reached_before_entry(
            side=-1,
            target=95.0,
            high=99.0,
            low=95.0,
        )
        self.assertTrue(long_reached)
        self.assertEqual(long_reached, short_reached)
        self.assertFalse(
            frozen_target_reached_before_entry(
                side=1,
                target=105.0,
                high=104.9,
                low=101.0,
            ),
        )
        self.assertFalse(
            frozen_target_reached_before_entry(
                side=-1,
                target=95.0,
                high=99.0,
                low=95.1,
            ),
        )

    def test_frozen_milestone_revalidates_symmetrically(self) -> None:
        long_result = revalidate_frozen_milestone(
            side=1,
            entry=100.0,
            target=110.0,
            milestone=105.0,
            atr=2.0,
            stop_buffer_atr=0.10,
            cost_rate=0.001,
            adverse_slippage_rate=0.0005,
        )
        short_result = revalidate_frozen_milestone(
            side=-1,
            entry=100.0,
            target=90.0,
            milestone=95.0,
            atr=2.0,
            stop_buffer_atr=0.10,
            cost_rate=0.001,
            adverse_slippage_rate=0.0005,
        )
        self.assertIsNotNone(long_result)
        self.assertIsNotNone(short_result)
        assert long_result is not None and short_result is not None
        self.assertGreater(long_result[1], 0.0)
        self.assertGreater(short_result[1], 0.0)
        self.assertAlmostEqual(long_result[0] - 100.0, 100.0 - short_result[0])

    def test_milestone_is_not_replaced_after_entry_passes_it(self) -> None:
        self.assertIsNone(
            revalidate_frozen_milestone(
                side=1,
                entry=105.1,
                target=110.0,
                milestone=105.0,
                atr=2.0,
                stop_buffer_atr=0.10,
                cost_rate=0.001,
                adverse_slippage_rate=0.0005,
            ),
        )
        self.assertIsNone(
            revalidate_frozen_milestone(
                side=-1,
                entry=94.9,
                target=90.0,
                milestone=95.0,
                atr=2.0,
                stop_buffer_atr=0.10,
                cost_rate=0.001,
                adverse_slippage_rate=0.0005,
            ),
        )

    def test_milestone_must_still_lock_positive_net(self) -> None:
        self.assertIsNone(
            revalidate_frozen_milestone(
                side=1,
                entry=104.95,
                target=110.0,
                milestone=105.0,
                atr=2.0,
                stop_buffer_atr=0.10,
                cost_rate=0.001,
                adverse_slippage_rate=0.0005,
            ),
        )

    def test_nonfinite_target_is_not_completed(self) -> None:
        self.assertFalse(
            frozen_target_reached_before_entry(
                side=1,
                target=math.nan,
                high=105.0,
                low=100.0,
            ),
        )


if __name__ == "__main__":
    unittest.main()
