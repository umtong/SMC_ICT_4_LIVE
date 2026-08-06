from __future__ import annotations

import math
import unittest

from balance_acceptance_phase_logic import EARLY_RESET_REACCELERATION
from balance_acceptance_phase_logic import INVALID_OBSERVATION
from balance_acceptance_phase_logic import MATURE_AT_BREAKOUT
from balance_acceptance_phase_logic import NO_BROAD_FLOW_RESET
from balance_acceptance_phase_logic import NO_DIRECTIONAL_BREAKOUT
from balance_acceptance_phase_logic import NO_TAIL_REACCELERATION
from balance_acceptance_phase_logic import TWO_TO_ONE_AGGRESSOR_SHARE
from balance_acceptance_phase_logic import position_building_flow_phase
from balance_acceptance_phase_logic import position_building_flow_phase_ready


class BalanceAcceptancePhaseLogicTest(unittest.TestCase):
    def test_week2_like_reset_and_reacceleration_passes(self) -> None:
        phase = position_building_flow_phase(
            side=1,
            breakout_flow_3m=0.1298352883,
            retest_flow_3m=0.0122025943,
            retest_flow_15s=0.4231333912,
        )
        self.assertEqual(phase, EARLY_RESET_REACCELERATION)
        self.assertTrue(
            position_building_flow_phase_ready(
                side=1,
                breakout_flow_3m=0.1298352883,
                retest_flow_3m=0.0122025943,
                retest_flow_15s=0.4231333912,
            ),
        )

    def test_week3_like_mature_breakout_is_rejected(self) -> None:
        self.assertEqual(
            position_building_flow_phase(
                side=1,
                breakout_flow_3m=0.4103462253,
                retest_flow_3m=0.4378848003,
                retest_flow_15s=0.9447581305,
            ),
            MATURE_AT_BREAKOUT,
        )

    def test_broad_flow_must_reset_before_retest(self) -> None:
        self.assertEqual(
            position_building_flow_phase(
                side=1,
                breakout_flow_3m=0.20,
                retest_flow_3m=0.25,
                retest_flow_15s=0.40,
            ),
            NO_BROAD_FLOW_RESET,
        )
        self.assertEqual(
            position_building_flow_phase(
                side=1,
                breakout_flow_3m=0.20,
                retest_flow_3m=-0.01,
                retest_flow_15s=0.40,
            ),
            NO_BROAD_FLOW_RESET,
        )

    def test_tail_must_reaccelerate_above_cooled_broad_flow(self) -> None:
        self.assertEqual(
            position_building_flow_phase(
                side=1,
                breakout_flow_3m=0.20,
                retest_flow_3m=0.10,
                retest_flow_15s=0.10,
            ),
            NO_TAIL_REACCELERATION,
        )
        self.assertEqual(
            position_building_flow_phase(
                side=1,
                breakout_flow_3m=0.20,
                retest_flow_3m=0.10,
                retest_flow_15s=-0.01,
            ),
            NO_TAIL_REACCELERATION,
        )

    def test_direction_and_mirror_symmetry(self) -> None:
        long_phase = position_building_flow_phase(
            side=1,
            breakout_flow_3m=0.20,
            retest_flow_3m=0.05,
            retest_flow_15s=0.30,
        )
        short_phase = position_building_flow_phase(
            side=-1,
            breakout_flow_3m=-0.20,
            retest_flow_3m=-0.05,
            retest_flow_15s=-0.30,
        )
        self.assertEqual(long_phase, EARLY_RESET_REACCELERATION)
        self.assertEqual(long_phase, short_phase)
        self.assertEqual(
            position_building_flow_phase(
                side=1,
                breakout_flow_3m=-0.01,
                retest_flow_3m=0.0,
                retest_flow_15s=0.20,
            ),
            NO_DIRECTIONAL_BREAKOUT,
        )

    def test_one_third_is_exactly_two_to_one_ratio(self) -> None:
        buy, sell = 2.0, 1.0
        normalized = (buy - sell) / (buy + sell)
        self.assertAlmostEqual(normalized, TWO_TO_ONE_AGGRESSOR_SHARE)
        self.assertEqual(
            position_building_flow_phase(
                side=1,
                breakout_flow_3m=normalized,
                retest_flow_3m=0.05,
                retest_flow_15s=0.20,
            ),
            MATURE_AT_BREAKOUT,
        )

    def test_nonfinite_observation_is_invalid(self) -> None:
        self.assertEqual(
            position_building_flow_phase(
                side=1,
                breakout_flow_3m=math.nan,
                retest_flow_3m=0.05,
                retest_flow_15s=0.20,
            ),
            INVALID_OBSERVATION,
        )

    def test_side_must_be_directional(self) -> None:
        with self.assertRaises(ValueError):
            position_building_flow_phase(
                side=0,
                breakout_flow_3m=0.20,
                retest_flow_3m=0.05,
                retest_flow_15s=0.20,
            )


if __name__ == "__main__":
    unittest.main()
