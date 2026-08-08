from __future__ import annotations

import unittest

from spot_price_discovery_logic import spot_context_accepted
from spot_price_discovery_logic import spot_context_invalidated
from spot_price_discovery_logic import spot_context_pullback_eligible
from spot_price_discovery_logic import spot_led_direction
from spot_price_discovery_logic import spot_pullback_transfer_ready


class SpotPriceDiscoveryLogicTests(unittest.TestCase):
    def test_spot_lead_is_mirror_symmetric(self) -> None:
        common = {
            "spot_ready": True,
            "spot_notional_burst": 1.5,
            "spot_efficiency_60s": 0.2,
        }
        self.assertEqual(
            spot_led_direction(
                **common,
                spot_flow_15s=0.2,
                spot_flow_60s=1.0 / 3.0,
                spot_ret_60s_bps=2.0,
                perp_minus_spot_return_bps=-0.1,
            ),
            1,
        )
        self.assertEqual(
            spot_led_direction(
                **common,
                spot_flow_15s=-0.2,
                spot_flow_60s=-1.0 / 3.0,
                spot_ret_60s_bps=-2.0,
                perp_minus_spot_return_bps=0.1,
            ),
            -1,
        )

    def test_perpetual_lead_is_not_mislabeled_spot_led(self) -> None:
        self.assertEqual(
            spot_led_direction(
                spot_ready=True,
                spot_flow_15s=0.3,
                spot_flow_60s=0.4,
                spot_notional_burst=2.0,
                spot_ret_60s_bps=4.0,
                spot_efficiency_60s=0.5,
                perp_minus_spot_return_bps=1.0,
            ),
            0,
        )

    def test_context_acceptance_and_invalidation_are_symmetric(self) -> None:
        self.assertTrue(
            spot_context_accepted(
                direction=1,
                boundary_close=100.0,
                favorable_extreme=100.5,
                atr=1.0,
            ),
        )
        self.assertTrue(
            spot_context_accepted(
                direction=-1,
                boundary_close=100.0,
                favorable_extreme=99.5,
                atr=1.0,
            ),
        )
        self.assertTrue(
            spot_context_invalidated(
                direction=1,
                boundary_low=99.5,
                boundary_high=100.5,
                current_close=99.29,
                atr=1.0,
            ),
        )
        self.assertTrue(
            spot_context_invalidated(
                direction=-1,
                boundary_low=99.5,
                boundary_high=100.5,
                current_close=100.71,
                atr=1.0,
            ),
        )

    def test_pullback_horizon_requires_accepted_new_context(self) -> None:
        self.assertFalse(spot_context_pullback_eligible(accepted=False, age_bars=10))
        self.assertFalse(spot_context_pullback_eligible(accepted=True, age_bars=2))
        self.assertTrue(spot_context_pullback_eligible(accepted=True, age_bars=3))
        self.assertTrue(spot_context_pullback_eligible(accepted=True, age_bars=60))
        self.assertFalse(spot_context_pullback_eligible(accepted=True, age_bars=61))

    def test_pullback_transfer_is_mirror_symmetric(self) -> None:
        self.assertTrue(
            spot_pullback_transfer_ready(
                direction=1,
                pool_kind="LOW",
                pool_level=100.0,
                previous_close=100.2,
                high=100.4,
                low=99.6,
                close=100.1,
                atr=1.0,
                flow_15s=0.3,
                flow_60s=-0.2,
                depth_imbalance=0.1,
                trade_vwap=100.0,
                spot_flow_3m=0.0,
            ),
        )
        self.assertTrue(
            spot_pullback_transfer_ready(
                direction=-1,
                pool_kind="HIGH",
                pool_level=100.0,
                previous_close=99.8,
                high=100.4,
                low=99.6,
                close=99.9,
                atr=1.0,
                flow_15s=-0.3,
                flow_60s=0.2,
                depth_imbalance=-0.1,
                trade_vwap=100.0,
                spot_flow_3m=0.0,
            ),
        )

    def test_each_transition_component_can_veto_pullback(self) -> None:
        base = {
            "direction": 1,
            "pool_kind": "LOW",
            "pool_level": 100.0,
            "previous_close": 100.2,
            "high": 100.4,
            "low": 99.6,
            "close": 100.1,
            "atr": 1.0,
            "flow_15s": 0.3,
            "flow_60s": -0.2,
            "depth_imbalance": 0.1,
            "trade_vwap": 100.0,
            "spot_flow_3m": 0.0,
        }
        for key, value in (
            ("pool_kind", "HIGH"),
            ("low", 99.8),
            ("close", 99.9),
            ("flow_15s", 0.29),
            ("depth_imbalance", 0.09),
            ("trade_vwap", 100.2),
            ("spot_flow_3m", -0.01),
        ):
            case = dict(base)
            case[key] = value
            self.assertFalse(spot_pullback_transfer_ready(**case), key)


if __name__ == "__main__":
    unittest.main()
