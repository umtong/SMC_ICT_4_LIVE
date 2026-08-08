from __future__ import annotations

import math
import unittest

from quarter_hour_logic import QuarterObservation
from quarter_hour_logic import QuarterRoute
from quarter_hour_logic import route_quarter_hour


def observation(**overrides):
    values = {
        "opening_flow_10s": 0.55,
        "opening_notional_burst": 1.25,
        "perpetual_return_bps": 8.0,
        "tail_flow_15s": 0.20,
        "spot_flow_60s": 0.15,
        "spot_return_bps": 5.0,
        "oi_change_5m": 0.01,
        "oi_value_change_5m": 0.02,
        "l1_pressure_persisted": True,
        "l1_pressure_flipped": False,
    }
    values.update(overrides)
    return QuarterObservation(**values)


class QuarterHourLogicTests(unittest.TestCase):
    def test_non_burst_is_not_an_event(self) -> None:
        decision = route_quarter_hour(observation(opening_notional_burst=1.0))
        self.assertEqual(decision.route, QuarterRoute.NO_EVENT)
        self.assertEqual(decision.side, 0)

    def test_new_risk_continuation(self) -> None:
        decision = route_quarter_hour(observation())
        self.assertEqual(decision.route, QuarterRoute.NEW_RISK_CONTINUATION)
        self.assertEqual(decision.parent_direction, 1)
        self.assertEqual(decision.side, 1)

    def test_forced_closure_reversal(self) -> None:
        decision = route_quarter_hour(
            observation(
                perpetual_return_bps=-7.0,
                tail_flow_15s=-0.30,
                spot_flow_60s=-0.20,
                spot_return_bps=-4.0,
                oi_change_5m=-0.01,
                oi_value_change_5m=-0.02,
                l1_pressure_persisted=False,
                l1_pressure_flipped=True,
            ),
        )
        self.assertEqual(decision.route, QuarterRoute.FORCED_CLOSURE_REVERSAL)
        self.assertEqual(decision.parent_direction, 1)
        self.assertEqual(decision.side, -1)

    def test_conflicting_state_is_unresolved(self) -> None:
        decision = route_quarter_hour(observation(spot_flow_60s=-0.1))
        self.assertEqual(decision.route, QuarterRoute.UNRESOLVED)
        self.assertEqual(decision.side, 0)

    def test_mirror_symmetry(self) -> None:
        long = route_quarter_hour(observation())
        short = route_quarter_hour(
            observation(
                opening_flow_10s=-0.55,
                perpetual_return_bps=-8.0,
                tail_flow_15s=-0.20,
                spot_flow_60s=-0.15,
                spot_return_bps=-5.0,
            ),
        )
        self.assertEqual(long.route, short.route)
        self.assertEqual(long.side, -short.side)

    def test_nonfinite_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            route_quarter_hour(observation(opening_flow_10s=math.nan))


if __name__ == "__main__":
    unittest.main()
