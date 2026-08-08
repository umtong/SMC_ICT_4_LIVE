from __future__ import annotations

import inspect
import unittest

from strategy_v46_no_post_retrace_breakaway import NoPostRetraceBreakawayStrategy
from strategy_v55_spot_price_discovery import BRANCH
from strategy_v55_spot_price_discovery import SpotLedPriceDiscoveryStrategy


class SpotLedPriceDiscoveryContractTests(unittest.TestCase):
    def test_v55_is_an_additive_subclass_of_frozen_v46(self) -> None:
        self.assertTrue(
            issubclass(
                SpotLedPriceDiscoveryStrategy,
                NoPostRetraceBreakawayStrategy,
            ),
        )

    def test_external_detector_is_delegated_then_context_is_restored(self) -> None:
        source = inspect.getsource(SpotLedPriceDiscoveryStrategy._detect_sweep)
        self.assertIn("super()._detect_sweep", source)
        self.assertIn("saved_quarter_context", source)
        self.assertIn("finally", source)
        self.assertIn("self.quarter_context = saved_quarter_context", source)
        self.assertIn('"INTERNAL_INVENTORY_TRAP"', source)

    def test_v55_reuses_inherited_price_capped_bracket(self) -> None:
        source = inspect.getsource(
            SpotLedPriceDiscoveryStrategy._submit_price_capped_bracket,
        )
        self.assertIn("super()._submit_price_capped_bracket", source)
        self.assertIn(BRANCH, source)
        self.assertNotIn("order_factory", source)
        self.assertNotIn("submit_order", source)

    def test_completed_spot_features_are_mandatory(self) -> None:
        source = inspect.getsource(SpotLedPriceDiscoveryStrategy.on_start)
        self.assertIn("_REQUIRED_SPOT_FEATURES", source)
        self.assertIn("spot price-discovery feature contract was not installed", source)


if __name__ == "__main__":
    unittest.main()
