from __future__ import annotations

import unittest

from failed_reversal_continuation_logic import continuation_reacceptance_ready
from failed_reversal_continuation_logic import first_continuation_retest_response


class FailedReversalContinuationLogicTest(unittest.TestCase):
    def reaccept(self, *, side=1, mirror=False, **overrides):
        values = {
            "continuation_side": side,
            "sweep_extreme": 100.0,
            "open_price": 100.2 if side > 0 else 99.8,
            "high": 101.2 if side > 0 else 100.1,
            "low": 99.9 if side > 0 else 98.8,
            "close": 101.0 if side > 0 else 99.0,
            "atr": 1.0,
            "flow_15s": 0.2 * side,
            "flow_60s": 0.2 * side,
            "efficiency_60s": 0.50,
            "notional_burst": 1.10,
            "bid_depth_change_1m": -0.02 if side < 0 else 0.01,
            "ask_depth_change_1m": -0.02 if side > 0 else 0.01,
            "minimum_close_distance_atr": 0.05,
            "minimum_flow": 0.10,
            "minimum_efficiency": 0.45,
            "minimum_notional_burst": 1.05,
            "minimum_depth_withdrawal": 0.01,
            "minimum_close_location": 0.62,
        }
        values.update(overrides)
        return continuation_reacceptance_ready(**values)

    def test_reacceptance_requires_price_flow_efficiency_activity_and_withdrawal(self) -> None:
        self.assertTrue(self.reaccept())
        self.assertFalse(self.reaccept(close=100.01))
        self.assertFalse(self.reaccept(flow_60s=0.05))
        self.assertFalse(self.reaccept(efficiency_60s=0.44))
        self.assertFalse(self.reaccept(notional_burst=1.04))
        self.assertFalse(self.reaccept(ask_depth_change_1m=-0.005))

    def test_reacceptance_is_mirror_symmetric(self) -> None:
        self.assertEqual(self.reaccept(side=1), self.reaccept(side=-1))

    def test_opposing_or_nonfinite_observation_is_not_reacceptance(self) -> None:
        self.assertFalse(self.reaccept(flow_15s=-0.2))
        self.assertFalse(self.reaccept(close=float("nan")))
        with self.assertRaises(ValueError):
            self.reaccept(side=0)

    def test_first_retest_requires_touch_defense_flow_and_depth(self) -> None:
        self.assertTrue(
            first_continuation_retest_response(
                continuation_side=1,
                sweep_extreme=100.0,
                high=101.0,
                low=99.9,
                close=100.5,
                flow_15s=-0.05,
                depth_imbalance=0.12,
                maximum_counterflow=0.08,
            ),
        )
        self.assertFalse(
            first_continuation_retest_response(
                continuation_side=1,
                sweep_extreme=100.0,
                high=101.0,
                low=100.1,
                close=100.5,
                flow_15s=-0.05,
                depth_imbalance=0.12,
                maximum_counterflow=0.08,
            ),
        )

    def test_first_retest_is_mirror_symmetric(self) -> None:
        long = first_continuation_retest_response(
            continuation_side=1,
            sweep_extreme=100.0,
            high=101.0,
            low=99.9,
            close=100.5,
            flow_15s=-0.05,
            depth_imbalance=0.12,
            maximum_counterflow=0.08,
        )
        short = first_continuation_retest_response(
            continuation_side=-1,
            sweep_extreme=100.0,
            high=100.1,
            low=99.0,
            close=99.5,
            flow_15s=0.05,
            depth_imbalance=-0.12,
            maximum_counterflow=0.08,
        )
        self.assertEqual(long, short)


if __name__ == "__main__":
    unittest.main()
