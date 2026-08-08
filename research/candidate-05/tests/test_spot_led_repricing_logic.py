from __future__ import annotations

import math
import unittest

from spot_led_repricing_logic import SPOT_CONTEXT_MAX_AGE_BARS
from spot_led_repricing_logic import SPOT_CONTEXT_MIN_AGE_BARS
from spot_led_repricing_logic import spot_context_accepted
from spot_led_repricing_logic import spot_context_entry_eligible
from spot_led_repricing_logic import spot_context_invalidated
from spot_led_repricing_logic import spot_led_repricing_direction


class SpotLedRepricingLogicTests(unittest.TestCase):
    def test_spot_lead_is_mirror_symmetric(self) -> None:
        common = {
            "spot_notional_burst": 1.8,
            "spot_efficiency": 0.35,
        }
        self.assertEqual(
            spot_led_repricing_direction(
                spot_flow_15s=0.50,
                spot_flow_60s=0.30,
                spot_return_bps=5.0,
                perpetual_return_bps=2.0,
                **common,
            ),
            1,
        )
        self.assertEqual(
            spot_led_repricing_direction(
                spot_flow_15s=-0.50,
                spot_flow_60s=-0.30,
                spot_return_bps=-5.0,
                perpetual_return_bps=-2.0,
                **common,
            ),
            -1,
        )

    def test_each_price_discovery_component_can_veto(self) -> None:
        base = {
            "spot_flow_15s": 0.50,
            "spot_flow_60s": 0.30,
            "spot_notional_burst": 1.8,
            "spot_return_bps": 5.0,
            "spot_efficiency": 0.35,
            "perpetual_return_bps": 2.0,
        }
        mutations = {
            "spot_flow_15s": 0.20,
            "spot_flow_60s": 0.10,
            "spot_notional_burst": 1.20,
            "spot_return_bps": 1.0,
            "spot_efficiency": 0.10,
            "perpetual_return_bps": 4.5,
        }
        for key, value in mutations.items():
            with self.subTest(key=key):
                trial = dict(base)
                trial[key] = value
                self.assertEqual(spot_led_repricing_direction(**trial), 0)

    def test_acceptance_and_invalidation_are_symmetric(self) -> None:
        self.assertTrue(
            spot_context_accepted(
                direction=1,
                boundary_close=100.0,
                favorable_extreme=101.0,
                atr=2.0,
                perpetual_flow_3m=0.05,
            ),
        )
        self.assertTrue(
            spot_context_accepted(
                direction=-1,
                boundary_close=100.0,
                favorable_extreme=99.0,
                atr=2.0,
                perpetual_flow_3m=-0.05,
            ),
        )
        self.assertTrue(
            spot_context_invalidated(
                direction=1,
                boundary_low=99.0,
                boundary_high=101.0,
                current_close=98.5,
                atr=2.0,
            ),
        )
        self.assertTrue(
            spot_context_invalidated(
                direction=-1,
                boundary_low=99.0,
                boundary_high=101.0,
                current_close=101.5,
                atr=2.0,
            ),
        )

    def test_entry_requires_alignment_and_mature_finite_horizon(self) -> None:
        self.assertTrue(
            spot_context_entry_eligible(
                setup_side=1,
                context_direction=1,
                context_age_bars=SPOT_CONTEXT_MIN_AGE_BARS,
                context_accepted=True,
            ),
        )
        self.assertFalse(
            spot_context_entry_eligible(
                setup_side=-1,
                context_direction=1,
                context_age_bars=SPOT_CONTEXT_MIN_AGE_BARS,
                context_accepted=True,
            ),
        )
        self.assertFalse(
            spot_context_entry_eligible(
                setup_side=1,
                context_direction=1,
                context_age_bars=SPOT_CONTEXT_MIN_AGE_BARS - 1,
                context_accepted=True,
            ),
        )
        self.assertFalse(
            spot_context_entry_eligible(
                setup_side=1,
                context_direction=1,
                context_age_bars=SPOT_CONTEXT_MAX_AGE_BARS + 1,
                context_accepted=True,
            ),
        )

    def test_non_finite_inputs_never_create_a_signal(self) -> None:
        self.assertEqual(
            spot_led_repricing_direction(
                spot_flow_15s=math.nan,
                spot_flow_60s=0.5,
                spot_notional_burst=2.0,
                spot_return_bps=5.0,
                spot_efficiency=0.5,
                perpetual_return_bps=1.0,
            ),
            0,
        )


if __name__ == "__main__":
    unittest.main()
