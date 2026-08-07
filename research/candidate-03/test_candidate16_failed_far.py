from __future__ import annotations

import unittest

from candidate16_failed_far import (
    FailedFarState,
    continuation_direction,
    defended_boundary_retest,
    market_economics,
    outside_acceptance,
)
from logic import BarObs, Direction, Side


def state(side: Side) -> FailedFarState:
    return FailedFarState(
        scenario_id="C16",
        parent_scenario_id="PARENT",
        side=side,
        direction=continuation_direction(side),
        boundary=100.0,
        target_pool_id="TARGET",
        target_price=110.0 if side == Side.HIGH else 90.0,
        source_pool_level=99.5 if side == Side.HIGH else 100.5,
        source_pool_source="TEST",
        source_strength=2,
        failure_ts_ns=1,
        failure_index=1,
        expiry_index=100,
        original_entry=99.0 if side == Side.HIGH else 101.0,
        original_stop=101.0 if side == Side.HIGH else 99.0,
        original_target=95.0 if side == Side.HIGH else 105.0,
    )


class FailedFarStateTests(unittest.TestCase):
    def test_continuation_direction_is_same_as_swept_side(self) -> None:
        self.assertEqual(continuation_direction(Side.HIGH), Direction.LONG)
        self.assertEqual(continuation_direction(Side.LOW), Direction.SHORT)

    def test_two_sided_acceptance_is_symmetric(self) -> None:
        long_bar = BarObs(1, 100.0, 101.2, 99.9, 100.8, 100.0, 70.0)
        short_bar = BarObs(1, 100.0, 100.1, 98.8, 99.2, 100.0, 30.0)
        self.assertTrue(outside_acceptance(state(Side.HIGH), long_bar, 1.0, 0.02))
        self.assertTrue(outside_acceptance(state(Side.LOW), short_bar, 1.0, 0.02))

    def test_completed_boundary_retest_must_touch_and_close_outside(self) -> None:
        long = state(Side.HIGH)
        defended_long = BarObs(1, 100.8, 101.0, 100.1, 100.7, 100.0, 60.0)
        rejected_long = BarObs(1, 100.8, 101.0, 99.6, 99.8, 100.0, 40.0)
        self.assertTrue(
            defended_boundary_retest(long, defended_long, 1.0, hold_atr=0.02, retest_atr=0.18)
        )
        self.assertFalse(
            defended_boundary_retest(long, rejected_long, 1.0, hold_atr=0.02, retest_atr=0.18)
        )

        short = state(Side.LOW)
        defended_short = BarObs(1, 99.2, 99.9, 99.0, 99.3, 100.0, 40.0)
        rejected_short = BarObs(1, 99.2, 100.4, 99.0, 100.2, 100.0, 60.0)
        self.assertTrue(
            defended_boundary_retest(short, defended_short, 1.0, hold_atr=0.02, retest_atr=0.18)
        )
        self.assertFalse(
            defended_boundary_retest(short, rejected_short, 1.0, hold_atr=0.02, retest_atr=0.18)
        )

    def test_market_economics_is_directionally_symmetric(self) -> None:
        long = market_economics(
            direction=Direction.LONG,
            entry=101.0,
            stop=99.0,
            target=106.0,
            taker_rate=0.0008,
            target_maker_rate=0.0004,
        )
        short = market_economics(
            direction=Direction.SHORT,
            entry=99.0,
            stop=101.0,
            target=94.0,
            taker_rate=0.0008,
            target_maker_rate=0.0004,
        )
        self.assertGreater(long[0], 0.0)
        self.assertGreater(short[0], 0.0)
        self.assertGreater(long[3], 1.25)
        self.assertGreater(short[3], 1.25)
        self.assertAlmostEqual(long[3], short[3], delta=0.03)


if __name__ == "__main__":
    unittest.main(verbosity=2)
