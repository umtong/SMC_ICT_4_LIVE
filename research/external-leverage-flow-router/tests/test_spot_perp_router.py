from __future__ import annotations

import unittest

from spot_perp_router import (
    ParticipationRoute,
    classify_parent_exhaustion,
    exhaustion_transition_confirmed,
)


class SpotPerpRouterTests(unittest.TestCase):
    def test_parent_requires_cross_market_climax_at_external_liquidity(self):
        decision = classify_parent_exhaustion(
            direction=1,
            perp_return_bps=48.0,
            atr_bps=9.0,
            perp_flow=0.44,
            spot_return_bps=41.0,
            spot_flow=0.35,
            notional_burst=8.0,
            efficiency=0.08,
            perp_touched_external_edge=True,
            spot_touched_external_edge=True,
            min_return_bps=30.0,
            min_displacement_atr=4.0,
            min_perp_flow=0.30,
            min_spot_flow=0.20,
            min_notional_burst=5.0,
            max_efficiency=0.15,
        )
        self.assertIs(
            decision.route,
            ParticipationRoute.EXTERNAL_LIQUIDITY_EXHAUSTION,
        )

    def test_directionally_efficient_shock_is_not_assumed_to_reverse(self):
        decision = classify_parent_exhaustion(
            direction=-1,
            perp_return_bps=-55.0,
            atr_bps=10.0,
            perp_flow=-0.51,
            spot_return_bps=-47.0,
            spot_flow=-0.39,
            notional_burst=9.0,
            efficiency=0.42,
            perp_touched_external_edge=True,
            spot_touched_external_edge=True,
            min_return_bps=30.0,
            min_displacement_atr=4.0,
            min_perp_flow=0.30,
            min_spot_flow=0.20,
            min_notional_burst=5.0,
            max_efficiency=0.15,
        )
        self.assertIs(decision.route, ParticipationRoute.UNRESOLVED)

    def test_transition_requires_later_structure_and_flow_reversal(self):
        self.assertTrue(
            exhaustion_transition_confirmed(
                event_direction=1,
                close=98.5,
                prior_high=102.0,
                prior_low=99.0,
                perp_flow=-0.12,
            ),
        )
        self.assertFalse(
            exhaustion_transition_confirmed(
                event_direction=1,
                close=98.5,
                prior_high=102.0,
                prior_low=99.0,
                perp_flow=0.12,
            ),
        )


if __name__ == "__main__":
    unittest.main()
