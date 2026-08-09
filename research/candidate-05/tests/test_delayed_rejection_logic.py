from __future__ import annotations

import math
import unittest

from delayed_rejection_logic import DELAYED_CHOCH_BARS
from delayed_rejection_logic import DELAYED_RESPONSE_BARS
from delayed_rejection_logic import delayed_access_is_material
from delayed_rejection_logic import delayed_rejection_response


class DelayedRejectionLogicTest(unittest.TestCase):
    def test_horizons_follow_existing_completed_flow_states(self) -> None:
        self.assertEqual(DELAYED_RESPONSE_BARS, 3)
        self.assertEqual(DELAYED_CHOCH_BARS, 4)

    def test_material_access_requires_penetration_and_activity(self) -> None:
        self.assertTrue(
            delayed_access_is_material(
                penetration_atr=0.10,
                notional_burst=1.20,
                minimum_penetration_atr=0.08,
                minimum_notional_burst=1.05,
            ),
        )
        self.assertFalse(
            delayed_access_is_material(
                penetration_atr=0.10,
                notional_burst=1.00,
                minimum_penetration_atr=0.08,
                minimum_notional_burst=1.05,
            ),
        )

    def test_delayed_rejection_is_mirror_symmetric(self) -> None:
        high_rejection = delayed_rejection_response(
            side=-1,
            pool_kind="HIGH",
            boundary=100.0,
            close=99.8,
            flow_15s=-0.30,
            flow_60s=-0.10,
            depth_imbalance=-0.20,
        )
        low_rejection = delayed_rejection_response(
            side=1,
            pool_kind="LOW",
            boundary=100.0,
            close=100.2,
            flow_15s=0.30,
            flow_60s=0.10,
            depth_imbalance=0.20,
        )
        self.assertTrue(high_rejection)
        self.assertTrue(low_rejection)

    def test_response_requires_reclaim_tail_turn_and_current_depth(self) -> None:
        common = dict(
            side=-1,
            pool_kind="HIGH",
            boundary=100.0,
            flow_15s=-0.30,
            flow_60s=-0.10,
            depth_imbalance=-0.20,
        )
        self.assertFalse(delayed_rejection_response(**common, close=100.2))
        self.assertFalse(
            delayed_rejection_response(
                **{**common, "flow_15s": -0.15},
                close=99.8,
            ),
        )
        self.assertFalse(
            delayed_rejection_response(
                **{**common, "depth_imbalance": 0.20},
                close=99.8,
            ),
        )

    def test_invalid_observation_is_not_a_response(self) -> None:
        self.assertFalse(
            delayed_rejection_response(
                side=1,
                pool_kind="LOW",
                boundary=100.0,
                close=100.2,
                flow_15s=math.nan,
                flow_60s=0.10,
                depth_imbalance=0.20,
            ),
        )


if __name__ == "__main__":
    unittest.main()
