from __future__ import annotations

import inspect
import unittest

from strategy_v46_no_post_retrace_breakaway import NoPostRetraceBreakawayStrategy
from strategy_v55_spot_led_price_discovery import SpotLedPriceDiscoveryStrategy


class SpotLedPriceDiscoveryContractTests(unittest.TestCase):
    def test_v55_is_additive_over_unchanged_v46(self) -> None:
        self.assertTrue(
            issubclass(SpotLedPriceDiscoveryStrategy, NoPostRetraceBreakawayStrategy),
        )
        source = inspect.getsource(SpotLedPriceDiscoveryStrategy)
        self.assertIn("super().on_bar(bar)", source)
        self.assertIn("self.spot_internal_pools", source)
        self.assertIn(
            "SPOT_LED_PRICE_DISCOVERY_PULLBACK",
            inspect.getsource(__import__("strategy_v55_spot_led_price_discovery")),
        )

    def test_context_transition_and_execution_evidence_are_separate(self) -> None:
        source = inspect.getsource(SpotLedPriceDiscoveryStrategy)
        self.assertIn("spot_led_direction", source)
        self.assertIn("spot_context_accepted", source)
        self.assertIn("spot_pullback_transfer_ready", source)
        self.assertIn("_submit_price_capped_bracket", source)
        self.assertIn("NO_STILL_LIVE_EXTERNAL_LIQUIDITY_TARGET", source)

    def test_one_context_does_not_reset_on_same_direction_bursts(self) -> None:
        source = inspect.getsource(SpotLedPriceDiscoveryStrategy._update_spot_context)
        self.assertIn("if current.direction == direction", source)
        self.assertIn("return", source)
        self.assertIn("OPPOSITE_SPOT_INFORMATION_BURST_REPLACED_CONTEXT", source)


if __name__ == "__main__":
    unittest.main()
