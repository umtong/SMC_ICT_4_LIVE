from __future__ import annotations

from types import SimpleNamespace
import unittest

from easychart_re1_auction_router_v2 import DirectHorizontalFlipResponseFamily


class HorizontalFlipTimeframeContractTest(unittest.TestCase):
    def test_broad_context_bar_is_not_forwarded_to_local_flip_engine(self) -> None:
        family = DirectHorizontalFlipResponseFamily("BTCUSDT", 0.1)
        self.assertEqual(family.on_bar(60, SimpleNamespace()), [])
        self.assertEqual(family.diagnostics["accepted_timeframes"], (15, 5, 1))


if __name__ == "__main__":
    unittest.main()
