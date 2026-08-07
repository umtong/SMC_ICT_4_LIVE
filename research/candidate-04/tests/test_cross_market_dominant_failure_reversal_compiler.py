from __future__ import annotations

import unittest

import pandas as pd

import cross_market_dominant_failure_reversal_compiler as candidate


class DominantFailureTests(unittest.TestCase):
    def frame(self, failure_return: float) -> pd.DataFrame:
        return pd.DataFrame(
            {
                "close": [102.0, 99.0],
                "ret_60s_bps": [20.0, -failure_return],
                "flow_60s": [0.60, -0.50],
                "basis_change_5m": [1.0, -2.0],
                "notional_60s": [2_000.0, 3_000.0],
            }
        )

    def test_failure_stronger_than_expansion_is_confirmed(self) -> None:
        passed, details = candidate.dominant_expansion_failure(
            self.frame(30.0),
            event_index=0,
            index=1,
            leader_side=1,
            event_open=100.0,
        )
        self.assertTrue(passed)
        self.assertTrue(details["dominant_failure_displacement"])
        self.assertGreater(details["failure_to_expansion_return_ratio"], 1.0)

    def test_failure_equal_to_expansion_is_not_confirmed(self) -> None:
        passed, details = candidate.dominant_expansion_failure(
            self.frame(20.0),
            event_index=0,
            index=1,
            leader_side=1,
            event_open=100.0,
        )
        self.assertFalse(passed)
        self.assertFalse(details["dominant_failure_displacement"])
        self.assertEqual(details["failure_to_expansion_return_ratio"], 1.0)

    def test_failure_weaker_than_expansion_is_not_confirmed(self) -> None:
        passed, details = candidate.dominant_expansion_failure(
            self.frame(10.0),
            event_index=0,
            index=1,
            leader_side=1,
            event_open=100.0,
        )
        self.assertFalse(passed)
        self.assertLess(details["failure_to_expansion_return_ratio"], 1.0)


if __name__ == "__main__":
    unittest.main()
