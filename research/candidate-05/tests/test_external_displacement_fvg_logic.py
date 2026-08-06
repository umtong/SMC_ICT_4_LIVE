from __future__ import annotations

import math
import unittest

from external_displacement_fvg_logic import DisplacementGap
from external_displacement_fvg_logic import displacement_gap
from external_displacement_fvg_logic import first_retest_response
from external_displacement_fvg_logic import gap_invalidated
from external_displacement_fvg_logic import structural_gap_stop


class ExternalDisplacementFvgLogicTest(unittest.TestCase):
    def bullish_gap(self) -> DisplacementGap:
        gap = displacement_gap(
            side=1,
            first_high=100.0,
            first_low=99.0,
            impulse_open=99.8,
            impulse_high=102.0,
            impulse_low=99.7,
            impulse_close=101.8,
            third_high=103.0,
            third_low=100.8,
            atr=2.0,
            minimum_body_atr=0.25,
            minimum_close_location=0.58,
        )
        self.assertIsNotNone(gap)
        assert gap is not None
        return gap

    def test_completed_directional_gap_is_mirror_symmetric(self) -> None:
        long_gap = self.bullish_gap()
        anchor = 100.0
        short_gap = displacement_gap(
            side=-1,
            first_high=2 * anchor - 99.0,
            first_low=2 * anchor - 100.0,
            impulse_open=2 * anchor - 99.8,
            impulse_high=2 * anchor - 99.7,
            impulse_low=2 * anchor - 102.0,
            impulse_close=2 * anchor - 101.8,
            third_high=2 * anchor - 100.8,
            third_low=2 * anchor - 103.0,
            atr=2.0,
            minimum_body_atr=0.25,
            minimum_close_location=0.58,
        )
        self.assertIsNotNone(short_gap)
        assert short_gap is not None
        self.assertAlmostEqual(short_gap.lower, 2 * anchor - long_gap.upper)
        self.assertAlmostEqual(short_gap.upper, 2 * anchor - long_gap.lower)
        self.assertAlmostEqual(short_gap.midpoint, 2 * anchor - long_gap.midpoint)

    def test_overlap_or_weak_impulse_is_not_a_gap(self) -> None:
        self.assertIsNone(
            displacement_gap(
                side=1,
                first_high=100.0,
                first_low=99.0,
                impulse_open=100.0,
                impulse_high=100.5,
                impulse_low=99.8,
                impulse_close=100.1,
                third_high=101.0,
                third_low=99.9,
                atr=2.0,
                minimum_body_atr=0.25,
                minimum_close_location=0.58,
            ),
        )

    def test_retest_requires_touch_defense_flow_and_depth(self) -> None:
        gap = self.bullish_gap()
        self.assertTrue(
            first_retest_response(
                side=1,
                external_level=99.5,
                gap=gap,
                high=101.4,
                low=100.3,
                close=100.6,
                flow_15s=-0.02,
                depth_imbalance=0.2,
                maximum_counterflow=0.08,
            ),
        )
        self.assertFalse(
            first_retest_response(
                side=1,
                external_level=99.5,
                gap=gap,
                high=101.4,
                low=100.3,
                close=100.6,
                flow_15s=-0.2,
                depth_imbalance=0.2,
                maximum_counterflow=0.08,
            ),
        )
        self.assertFalse(
            first_retest_response(
                side=1,
                external_level=99.5,
                gap=gap,
                high=101.4,
                low=100.3,
                close=100.6,
                flow_15s=0.1,
                depth_imbalance=-0.2,
                maximum_counterflow=0.08,
            ),
        )

    def test_retest_and_invalidation_are_mirror_symmetric(self) -> None:
        long_gap = self.bullish_gap()
        anchor = 100.0
        short_gap = DisplacementGap(
            side=-1,
            lower=2 * anchor - long_gap.upper,
            upper=2 * anchor - long_gap.lower,
            midpoint=2 * anchor - long_gap.midpoint,
        )
        long_ready = first_retest_response(
            side=1,
            external_level=99.5,
            gap=long_gap,
            high=101.4,
            low=100.3,
            close=100.6,
            flow_15s=0.02,
            depth_imbalance=0.2,
            maximum_counterflow=0.08,
        )
        short_ready = first_retest_response(
            side=-1,
            external_level=2 * anchor - 99.5,
            gap=short_gap,
            high=2 * anchor - 100.3,
            low=2 * anchor - 101.4,
            close=2 * anchor - 100.6,
            flow_15s=-0.02,
            depth_imbalance=-0.2,
            maximum_counterflow=0.08,
        )
        self.assertEqual(long_ready, short_ready)
        self.assertEqual(
            gap_invalidated(
                side=1,
                external_level=99.5,
                gap=long_gap,
                close=99.0,
            ),
            gap_invalidated(
                side=-1,
                external_level=2 * anchor - 99.5,
                gap=short_gap,
                close=2 * anchor - 99.0,
            ),
        )

    def test_structural_stop_is_mirror_symmetric(self) -> None:
        gap = self.bullish_gap()
        anchor = 100.0
        short_gap = DisplacementGap(
            side=-1,
            lower=2 * anchor - gap.upper,
            upper=2 * anchor - gap.lower,
            midpoint=2 * anchor - gap.midpoint,
        )
        long_stop = structural_gap_stop(
            side=1,
            external_level=99.5,
            gap=gap,
            atr=2.0,
            stop_buffer_atr=0.08,
        )
        short_stop = structural_gap_stop(
            side=-1,
            external_level=2 * anchor - 99.5,
            gap=short_gap,
            atr=2.0,
            stop_buffer_atr=0.08,
        )
        self.assertAlmostEqual(short_stop, 2 * anchor - long_stop)

    def test_nonfinite_observations_are_rejected(self) -> None:
        gap = self.bullish_gap()
        self.assertFalse(
            first_retest_response(
                side=1,
                external_level=99.5,
                gap=gap,
                high=101.0,
                low=100.0,
                close=100.8,
                flow_15s=math.nan,
                depth_imbalance=0.2,
                maximum_counterflow=0.08,
            ),
        )


if __name__ == "__main__":
    unittest.main()
