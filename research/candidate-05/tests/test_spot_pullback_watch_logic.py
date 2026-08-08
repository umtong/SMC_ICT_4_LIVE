from __future__ import annotations

import unittest

from spot_pullback_watch_logic import pullback_response_expired
from spot_pullback_watch_logic import spot_pullback_defense_ready
from spot_pullback_watch_logic import update_pullback_extreme


class SpotPullbackWatchLogicTests(unittest.TestCase):
    def test_extreme_update_is_mirror_symmetric(self) -> None:
        self.assertEqual(
            update_pullback_extreme(
                direction=1,
                current_extreme=99.8,
                high=100.2,
                low=99.5,
            ),
            99.5,
        )
        self.assertEqual(
            update_pullback_extreme(
                direction=-1,
                current_extreme=100.2,
                high=100.5,
                low=99.8,
            ),
            100.5,
        )

    def test_response_window_is_exactly_three_completed_bars(self) -> None:
        self.assertFalse(pullback_response_expired(age_bars=0))
        self.assertFalse(pullback_response_expired(age_bars=1))
        self.assertFalse(pullback_response_expired(age_bars=2))
        self.assertTrue(pullback_response_expired(age_bars=3))

    def test_defense_is_mirror_symmetric(self) -> None:
        self.assertTrue(
            spot_pullback_defense_ready(
                direction=1,
                level=100.0,
                close=100.2,
                flow_15s=0.3,
                flow_60s=-0.2,
                depth_imbalance=0.1,
                trade_vwap=100.1,
                spot_flow_3m=0.0,
            ),
        )
        self.assertTrue(
            spot_pullback_defense_ready(
                direction=-1,
                level=100.0,
                close=99.8,
                flow_15s=-0.3,
                flow_60s=0.2,
                depth_imbalance=-0.1,
                trade_vwap=99.9,
                spot_flow_3m=0.0,
            ),
        )

    def test_each_independent_component_can_veto_defense(self) -> None:
        base = {
            "direction": 1,
            "level": 100.0,
            "close": 100.2,
            "flow_15s": 0.3,
            "flow_60s": -0.2,
            "depth_imbalance": 0.1,
            "trade_vwap": 100.1,
            "spot_flow_3m": 0.0,
        }
        for key, value in (
            ("close", 99.9),
            ("flow_15s", 0.29),
            ("depth_imbalance", 0.09),
            ("trade_vwap", 100.3),
            ("spot_flow_3m", -0.01),
        ):
            case = dict(base)
            case[key] = value
            self.assertFalse(spot_pullback_defense_ready(**case), key)


if __name__ == "__main__":
    unittest.main()
