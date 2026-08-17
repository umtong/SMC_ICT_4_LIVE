from __future__ import annotations

import importlib.util
import unittest


_REPO_AVAILABLE = all(
    importlib.util.find_spec(name) is not None
    for name in ("contracts_v5", "easychart_re1_auction_router_v2")
)


@unittest.skipUnless(_REPO_AVAILABLE, "full EasyChart repository modules not available")
class TimeframeContractTest(unittest.TestCase):
    def test_local_sources_ignore_parent_60m_context_bars(self) -> None:
        from candidate_bundle_ml2 import ML2MatureDiagonalResponseFamily
        from easychart_re1_auction_router_v2 import DirectHorizontalFlipResponseFamily

        horizontal = object.__new__(DirectHorizontalFlipResponseFamily)
        self.assertEqual(horizontal.on_bar(60, object()), [])
        diagonal = object.__new__(ML2MatureDiagonalResponseFamily)
        diagonal._counts = {}
        self.assertEqual(diagonal.on_bar(60, object()), [])
        self.assertEqual(diagonal._counts["ignored_unsupported_timeframe"], 1)


if __name__ == "__main__":
    unittest.main()
