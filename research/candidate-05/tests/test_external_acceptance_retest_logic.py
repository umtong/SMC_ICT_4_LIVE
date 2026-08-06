from __future__ import annotations

import math
import unittest

from external_acceptance_retest_logic import accepted_level_invalidated
from external_acceptance_retest_logic import external_level_structural_stop
from external_acceptance_retest_logic import first_accepted_level_retest_response


class ExternalAcceptanceRetestLogicTest(unittest.TestCase):
    def test_first_retest_is_mirror_symmetric(self) -> None:
        anchor = 100.0
        long_ready = first_accepted_level_retest_response(
            side=1,
            level=99.5,
            high=100.4,
            low=99.3,
            close=100.1,
            flow_15s=0.03,
            depth_imbalance=0.2,
            maximum_counterflow=0.08,
        )
        short_ready = first_accepted_level_retest_response(
            side=-1,
            level=2 * anchor - 99.5,
            high=2 * anchor - 99.3,
            low=2 * anchor - 100.4,
            close=2 * anchor - 100.1,
            flow_15s=-0.03,
            depth_imbalance=-0.2,
            maximum_counterflow=0.08,
        )
        self.assertTrue(long_ready)
        self.assertEqual(long_ready, short_ready)

    def test_retest_requires_touch_close_flow_and_depth(self) -> None:
        base = dict(
            side=1,
            level=100.0,
            high=101.0,
            low=99.8,
            close=100.5,
            flow_15s=0.0,
            depth_imbalance=0.2,
            maximum_counterflow=0.08,
        )
        self.assertTrue(first_accepted_level_retest_response(**base))
        self.assertFalse(first_accepted_level_retest_response(**(base | {"low": 100.1})))
        self.assertFalse(first_accepted_level_retest_response(**(base | {"close": 99.9})))
        self.assertFalse(first_accepted_level_retest_response(**(base | {"flow_15s": -0.2})))
        self.assertFalse(first_accepted_level_retest_response(**(base | {"depth_imbalance": -0.2})))

    def test_invalidation_and_stop_are_mirror_symmetric(self) -> None:
        anchor = 100.0
        self.assertEqual(
            accepted_level_invalidated(side=1, level=99.5, close=99.4),
            accepted_level_invalidated(
                side=-1,
                level=2 * anchor - 99.5,
                close=2 * anchor - 99.4,
            ),
        )
        long_stop = external_level_structural_stop(
            side=1,
            level=99.5,
            atr=2.0,
            stop_buffer_atr=0.08,
        )
        short_stop = external_level_structural_stop(
            side=-1,
            level=2 * anchor - 99.5,
            atr=2.0,
            stop_buffer_atr=0.08,
        )
        self.assertAlmostEqual(short_stop, 2 * anchor - long_stop)

    def test_nonfinite_observation_is_not_ready(self) -> None:
        self.assertFalse(
            first_accepted_level_retest_response(
                side=1,
                level=100.0,
                high=101.0,
                low=99.5,
                close=100.4,
                flow_15s=math.nan,
                depth_imbalance=0.2,
                maximum_counterflow=0.08,
            ),
        )


if __name__ == "__main__":
    unittest.main()
