from __future__ import annotations

import unittest

from sequential_retest_ablation_logic import first_sequential_boundary_retest_without_depth
from strategy_v35b_directional_sequential_flow import DirectionalSequentialFlowRegimeStrategy
from strategy_v35c_retest_depth_ablation import SequentialRetestDepthAblationStrategy


class SequentialRetestDepthAblationTest(unittest.TestCase):
    def test_flow_only_ablation_preserves_first_touch_and_reclaim(self) -> None:
        self.assertTrue(
            first_sequential_boundary_retest_without_depth(
                side=1,
                boundary=100.0,
                high=102.0,
                low=99.9,
                close=101.0,
                flow_15s=0.01,
                maximum_counterflow=0.08,
            ),
        )
        self.assertFalse(
            first_sequential_boundary_retest_without_depth(
                side=1,
                boundary=100.0,
                high=102.0,
                low=99.9,
                close=99.8,
                flow_15s=0.2,
                maximum_counterflow=0.08,
            ),
        )

    def test_ablation_changes_only_retest_resolution_layer(self) -> None:
        self.assertTrue(
            issubclass(SequentialRetestDepthAblationStrategy, DirectionalSequentialFlowRegimeStrategy),
        )
        names = set(SequentialRetestDepthAblationStrategy.__dict__)
        self.assertEqual(names & {"_advance_sequential_watch"}, {"_advance_sequential_watch"})
        for forbidden in ("_advance_sequential_detector", "_arm_sequential_release", "_submit_sequential_release", "_equity_value"):
            self.assertNotIn(forbidden, names)


if __name__ == "__main__":
    unittest.main()
