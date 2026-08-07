from __future__ import annotations

import unittest

import pandas as pd

from basis_contract import _premium_features


class BasisContractTest(unittest.TestCase):
    def test_premium_changes_use_only_completed_observations(self) -> None:
        times = pd.date_range("2024-01-01", periods=7, freq="min", tz="UTC")
        frame = pd.DataFrame(
            {
                "close_time_dt": times,
                "close": [
                    -0.00040,
                    -0.00035,
                    -0.00030,
                    -0.00025,
                    -0.00020,
                    -0.00010,
                    -0.00005,
                ],
            },
        )
        result = _premium_features(frame)
        self.assertEqual(len(result), 7)
        self.assertTrue(result["premium_observed_time"].is_monotonic_increasing)
        self.assertAlmostEqual(result.iloc[5]["premium_change_5m"], 0.00030)


if __name__ == "__main__":
    unittest.main()
