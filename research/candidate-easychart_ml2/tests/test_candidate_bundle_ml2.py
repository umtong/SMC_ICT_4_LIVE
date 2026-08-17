from __future__ import annotations

import importlib.util
import unittest


_REPO_AVAILABLE = all(
    importlib.util.find_spec(name) is not None
    for name in ("contracts_v5", "easychart_re1_complete_bot_policy_v2")
)


@unittest.skipUnless(_REPO_AVAILABLE, "full EasyChart repository modules not available")
class CandidateBundleTest(unittest.TestCase):
    def test_complete_bundle_keeps_local_diagonal_scale_contract(self) -> None:
        from candidate_bundle_ml2 import EasyChartML2CandidateBundle

        bundle = EasyChartML2CandidateBundle("BTCUSDT", 0.1)
        self.assertEqual(
            bundle.mature_diagonal_acceptance.SUPPORTED_TIMEFRAMES,
            frozenset((15, 5, 1)),
        )
        self.assertEqual(
            bundle._BROAD_FACTOR_VETO_ENGINES,
            ("local_continuation", "efficient_pullback", "macro_trend_pullback"),
        )


if __name__ == "__main__":
    unittest.main()
