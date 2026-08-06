from __future__ import annotations

import math
import unittest

from impact_resiliency_reversal_logic import failed_break_reaccepted
from impact_resiliency_reversal_logic import failed_break_structural_stop
from impact_resiliency_reversal_logic import first_failed_break_retest_response
from impact_resiliency_reversal_logic import impact_failure_ready


class ImpactResiliencyReversalLogicTest(unittest.TestCase):
    def test_impact_failure_is_mirror_symmetric(self) -> None:
        anchor = 100.0
        long_shock_failure = impact_failure_ready(
            shock_side=1,
            external_level=100.0,
            shock_high=102.0,
            shock_low=99.5,
            close=99.4,
            flow_15s=-0.2,
            efficiency_60s=0.25,
            bid_depth_change_1m=-0.05,
            ask_depth_change_1m=0.08,
            maximum_efficiency=0.38,
            minimum_reversal_flow=0.12,
            minimum_depth_refill=0.01,
        )
        short_shock_failure = impact_failure_ready(
            shock_side=-1,
            external_level=2 * anchor - 100.0,
            shock_high=2 * anchor - 99.5,
            shock_low=2 * anchor - 102.0,
            close=2 * anchor - 99.4,
            flow_15s=0.2,
            efficiency_60s=0.25,
            bid_depth_change_1m=0.08,
            ask_depth_change_1m=-0.05,
            maximum_efficiency=0.38,
            minimum_reversal_flow=0.12,
            minimum_depth_refill=0.01,
        )
        self.assertTrue(long_shock_failure)
        self.assertEqual(long_shock_failure, short_shock_failure)

    def test_failure_requires_reclaim_low_efficiency_reversal_flow_and_refill(self) -> None:
        base = dict(
            shock_side=1,
            external_level=100.0,
            shock_high=102.0,
            shock_low=99.5,
            close=99.4,
            flow_15s=-0.2,
            efficiency_60s=0.25,
            bid_depth_change_1m=-0.05,
            ask_depth_change_1m=0.08,
            maximum_efficiency=0.38,
            minimum_reversal_flow=0.12,
            minimum_depth_refill=0.01,
        )
        self.assertTrue(impact_failure_ready(**base))
        self.assertFalse(impact_failure_ready(**(base | {"close": 100.1})))
        self.assertFalse(impact_failure_ready(**(base | {"efficiency_60s": 0.5})))
        self.assertFalse(impact_failure_ready(**(base | {"flow_15s": -0.05})))
        self.assertFalse(impact_failure_ready(**(base | {"ask_depth_change_1m": 0.0})))

    def test_first_failed_break_retest_is_mirror_symmetric(self) -> None:
        anchor = 100.0
        short_ready = first_failed_break_retest_response(
            trade_side=-1,
            external_level=100.0,
            high=100.2,
            low=99.0,
            close=99.6,
            flow_15s=-0.03,
            depth_imbalance=-0.2,
            maximum_counterflow=0.08,
        )
        long_ready = first_failed_break_retest_response(
            trade_side=1,
            external_level=2 * anchor - 100.0,
            high=2 * anchor - 99.0,
            low=2 * anchor - 100.2,
            close=2 * anchor - 99.6,
            flow_15s=0.03,
            depth_imbalance=0.2,
            maximum_counterflow=0.08,
        )
        self.assertTrue(short_ready)
        self.assertEqual(short_ready, long_ready)

    def test_retest_requires_touch_defense_flow_and_depth(self) -> None:
        base = dict(
            trade_side=-1,
            external_level=100.0,
            high=100.2,
            low=99.0,
            close=99.6,
            flow_15s=-0.03,
            depth_imbalance=-0.2,
            maximum_counterflow=0.08,
        )
        self.assertTrue(first_failed_break_retest_response(**base))
        self.assertFalse(first_failed_break_retest_response(**(base | {"high": 99.9})))
        self.assertFalse(first_failed_break_retest_response(**(base | {"close": 100.1})))
        self.assertFalse(first_failed_break_retest_response(**(base | {"flow_15s": 0.2})))
        self.assertFalse(first_failed_break_retest_response(**(base | {"depth_imbalance": 0.2})))

    def test_reacceptance_and_stop_are_mirror_symmetric(self) -> None:
        anchor = 100.0
        self.assertEqual(
            failed_break_reaccepted(shock_side=1, external_level=100.0, close=100.1),
            failed_break_reaccepted(
                shock_side=-1,
                external_level=2 * anchor - 100.0,
                close=2 * anchor - 100.1,
            ),
        )
        short_stop = failed_break_structural_stop(
            trade_side=-1,
            shock_high=102.0,
            shock_low=99.5,
            atr=2.0,
            stop_buffer_atr=0.08,
        )
        long_stop = failed_break_structural_stop(
            trade_side=1,
            shock_high=2 * anchor - 99.5,
            shock_low=2 * anchor - 102.0,
            atr=2.0,
            stop_buffer_atr=0.08,
        )
        self.assertAlmostEqual(long_stop, 2 * anchor - short_stop)

    def test_nonfinite_observations_are_rejected(self) -> None:
        self.assertFalse(
            impact_failure_ready(
                shock_side=1,
                external_level=100.0,
                shock_high=102.0,
                shock_low=99.5,
                close=99.4,
                flow_15s=math.nan,
                efficiency_60s=0.25,
                bid_depth_change_1m=-0.05,
                ask_depth_change_1m=0.08,
                maximum_efficiency=0.38,
                minimum_reversal_flow=0.12,
                minimum_depth_refill=0.01,
            ),
        )
        self.assertFalse(
            first_failed_break_retest_response(
                trade_side=-1,
                external_level=100.0,
                high=100.2,
                low=99.0,
                close=99.6,
                flow_15s=-0.03,
                depth_imbalance=math.nan,
                maximum_counterflow=0.08,
            ),
        )


if __name__ == "__main__":
    unittest.main()
