from __future__ import annotations

import unittest

from spot_perp_router import (
    ParticipationRoute,
    classify_parent_participation,
    perp_crowding_failure_confirmed,
    spot_led_retest_confirmed,
)


class SpotPerpRouterTests(unittest.TestCase):
    def test_spot_led_requires_spot_edge_acceptance_and_no_premium_widening(self):
        decision = classify_parent_participation(
            direction=1,
            spot_accepted_edge=True,
            perp_return_bps=8.0,
            spot_return_bps=11.0,
            perp_flow=0.25,
            spot_flow=0.30,
            basis_change_bps=-0.4,
        )
        self.assertIs(decision.route, ParticipationRoute.SPOT_LED_ACCEPTANCE)

    def test_perp_led_requires_spot_nonconfirmation_and_premium_widening(self):
        decision = classify_parent_participation(
            direction=-1,
            spot_accepted_edge=False,
            perp_return_bps=-14.0,
            spot_return_bps=-5.0,
            perp_flow=-0.35,
            spot_flow=-0.08,
            basis_change_bps=-1.2,
        )
        self.assertIs(decision.route, ParticipationRoute.PERP_LED_CROWDING)

    def test_distinct_later_transition_predicates(self):
        self.assertTrue(
            spot_led_retest_confirmed(
                side=1,
                boundary=100.0,
                high=103.0,
                low=100.05,
                close=102.0,
                atr=2.0,
                touch_tolerance_atr=0.10,
                spot_flow=0.2,
                perp_flow=0.2,
                basis_change_bps=-0.1,
            ),
        )
        self.assertTrue(
            perp_crowding_failure_confirmed(
                event_direction=1,
                boundary=100.0,
                close=99.5,
                spot_flow=-0.05,
                perp_flow=-0.2,
                basis_change_bps=-0.8,
            ),
        )


if __name__ == "__main__":
    unittest.main()
