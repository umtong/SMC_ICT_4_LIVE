from __future__ import annotations

import inspect
import unittest

from strategy_v55_spot_led_price_discovery import SpotLedPriceDiscoveryStrategy
from strategy_v56_spot_pullback_watch import SpotPullbackWatchStrategy


class SpotPullbackWatchContractTests(unittest.TestCase):
    def test_v56_changes_only_pullback_resolution_over_v55(self) -> None:
        self.assertTrue(issubclass(SpotPullbackWatchStrategy, SpotLedPriceDiscoveryStrategy))
        self.assertIn(
            "def _try_spot_led_pullback",
            inspect.getsource(SpotPullbackWatchStrategy),
        )
        self.assertIn(
            "super()._submit_spot_led_pullback",
            inspect.getsource(SpotPullbackWatchStrategy),
        )

    def test_one_pool_one_episode_and_bounded_expiry_are_explicit(self) -> None:
        source = inspect.getsource(SpotPullbackWatchStrategy)
        self.assertIn("SpotPullbackDefenseWatch", source)
        self.assertIn("pullback_response_expired", source)
        self.assertIn("FIRST_INTERNAL_PENETRATION_FROZE_ONE_RESPONSE_EPISODE", source)
        self.assertIn("BOUNDED_SPOT_PULLBACK_DEFENSE_WINDOW_EXPIRED", source)
        self.assertNotIn("for later_pool", source)

    def test_episode_extreme_is_used_for_structural_stop_geometry(self) -> None:
        source = inspect.getsource(
            SpotPullbackWatchStrategy._advance_spot_pullback_watch,
        )
        self.assertIn("pullback_watch_episode_extreme", source)
        self.assertIn('execution_row["low"] = watch.extreme', source)
        self.assertIn('execution_row["high"] = watch.extreme', source)


if __name__ == "__main__":
    unittest.main()
