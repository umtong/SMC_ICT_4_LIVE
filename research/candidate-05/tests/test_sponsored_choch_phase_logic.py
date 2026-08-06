from __future__ import annotations

import math
import unittest

from sponsored_choch_logic import EARLY_CHOCH_DIRECTIONAL_FLOW_MAX
from sponsored_choch_logic import sponsored_choch_flow_phase_ready


class SponsoredChochFlowPhaseTest(unittest.TestCase):
    def test_one_third_is_two_to_one_aggressor_ratio(self) -> None:
        normalized = (2.0 - 1.0) / (2.0 + 1.0)
        self.assertAlmostEqual(EARLY_CHOCH_DIRECTIONAL_FLOW_MAX, normalized)

    def test_early_aligned_phase_is_mirror_symmetric(self) -> None:
        long_ready = sponsored_choch_flow_phase_ready(side=1, flow_3m=0.10)
        short_ready = sponsored_choch_flow_phase_ready(side=-1, flow_3m=-0.10)
        self.assertTrue(long_ready)
        self.assertEqual(long_ready, short_ready)

    def test_opposing_and_mature_flow_route_to_observation(self) -> None:
        self.assertFalse(sponsored_choch_flow_phase_ready(side=1, flow_3m=-0.01))
        self.assertFalse(
            sponsored_choch_flow_phase_ready(
                side=1,
                flow_3m=EARLY_CHOCH_DIRECTIONAL_FLOW_MAX,
            ),
        )
        self.assertFalse(sponsored_choch_flow_phase_ready(side=-1, flow_3m=0.01))
        self.assertFalse(
            sponsored_choch_flow_phase_ready(
                side=-1,
                flow_3m=-EARLY_CHOCH_DIRECTIONAL_FLOW_MAX,
            ),
        )

    def test_zero_is_early_but_nonfinite_is_not(self) -> None:
        self.assertTrue(sponsored_choch_flow_phase_ready(side=1, flow_3m=0.0))
        self.assertFalse(sponsored_choch_flow_phase_ready(side=1, flow_3m=math.nan))
        self.assertFalse(sponsored_choch_flow_phase_ready(side=1, flow_3m=math.inf))


if __name__ == "__main__":
    unittest.main()
