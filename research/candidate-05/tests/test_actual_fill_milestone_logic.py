from __future__ import annotations

import unittest

from scenario_target_logic import revalidate_frozen_milestone


class ActualFillMilestoneLogicTest(unittest.TestCase):
    def test_actual_fill_can_validate_milestone_rejected_by_submission_cap(self) -> None:
        common = dict(
            side=1,
            target=30_542.3,
            milestone=30_446.0,
            atr=8.063333333333333,
            stop_buffer_atr=0.08,
            cost_rate=0.00075,
            adverse_slippage_rate=0.00025,
        )
        self.assertIsNone(
            revalidate_frozen_milestone(
                entry=30_397.1,
                **common,
            ),
        )
        actual = revalidate_frozen_milestone(
            entry=30_387.06864675412,
            **common,
        )
        self.assertIsNotNone(actual)
        assert actual is not None
        self.assertGreater(actual[1], 0.0)

    def test_worse_actual_fill_does_not_activate_protection(self) -> None:
        self.assertIsNone(
            revalidate_frozen_milestone(
                side=1,
                entry=30_430.0,
                target=30_542.3,
                milestone=30_446.0,
                atr=8.063333333333333,
                stop_buffer_atr=0.08,
                cost_rate=0.00075,
                adverse_slippage_rate=0.00025,
            ),
        )


if __name__ == "__main__":
    unittest.main()
