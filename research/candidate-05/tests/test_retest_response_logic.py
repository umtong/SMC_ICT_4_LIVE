from __future__ import annotations

import math
import unittest

from retest_response_logic import retest_response_ready
from retest_response_logic import retest_touched


class RetestResponseLogicTest(unittest.TestCase):
    def test_touch_is_mirror_symmetric(self) -> None:
        self.assertTrue(
            retest_touched(
                side=1,
                reference_price=100.0,
                high=101.0,
                low=100.0,
            ),
        )
        self.assertTrue(
            retest_touched(
                side=-1,
                reference_price=100.0,
                high=100.0,
                low=99.0,
            ),
        )
        self.assertFalse(
            retest_touched(
                side=1,
                reference_price=100.0,
                high=101.0,
                low=100.1,
            ),
        )
        self.assertFalse(
            retest_touched(
                side=-1,
                reference_price=100.0,
                high=99.9,
                low=99.0,
            ),
        )

    def test_response_requires_touch_reclaim_flow_and_depth(self) -> None:
        base = dict(
            side=1,
            reference_price=100.0,
            high=101.0,
            low=99.8,
            close=100.8,
            flow_15s=0.20,
            depth_imbalance=0.15,
            minimum_directional_depth=0.10,
        )
        self.assertTrue(retest_response_ready(**base))
        self.assertFalse(
            retest_response_ready(**{**base, "low": 100.1}),
        )
        self.assertFalse(
            retest_response_ready(**{**base, "close": 99.9}),
        )
        self.assertFalse(
            retest_response_ready(**{**base, "flow_15s": -0.01}),
        )
        self.assertFalse(
            retest_response_ready(**{**base, "depth_imbalance": 0.09}),
        )

    def test_response_is_mirror_symmetric(self) -> None:
        long_ready = retest_response_ready(
            side=1,
            reference_price=100.0,
            high=101.0,
            low=99.8,
            close=100.8,
            flow_15s=0.20,
            depth_imbalance=0.15,
            minimum_directional_depth=0.10,
        )
        short_ready = retest_response_ready(
            side=-1,
            reference_price=100.0,
            high=100.2,
            low=99.0,
            close=99.2,
            flow_15s=-0.20,
            depth_imbalance=-0.15,
            minimum_directional_depth=0.10,
        )
        self.assertTrue(long_ready)
        self.assertEqual(long_ready, short_ready)

    def test_existing_depth_boundary_is_inclusive(self) -> None:
        self.assertTrue(
            retest_response_ready(
                side=1,
                reference_price=100.0,
                high=101.0,
                low=100.0,
                close=100.2,
                flow_15s=0.01,
                depth_imbalance=0.10,
                minimum_directional_depth=0.10,
            ),
        )

    def test_nonfinite_observation_is_not_ready(self) -> None:
        self.assertFalse(
            retest_response_ready(
                side=1,
                reference_price=100.0,
                high=101.0,
                low=100.0,
                close=100.2,
                flow_15s=math.nan,
                depth_imbalance=0.15,
                minimum_directional_depth=0.10,
            ),
        )


if __name__ == "__main__":
    unittest.main()
