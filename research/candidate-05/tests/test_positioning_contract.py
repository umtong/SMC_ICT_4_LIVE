from __future__ import annotations

import unittest

import pandas as pd

from positioning_contract import _positioning_features


class PositioningContractTest(unittest.TestCase):
    def test_metrics_observation_and_changes_are_causal(self) -> None:
        times = pd.date_range("2024-01-01", periods=7, freq="5min", tz="UTC")
        frame = pd.DataFrame(
            {
                "metrics_observed_time": times,
                "sum_open_interest": [100, 101, 102, 103, 104, 105, 106],
                "sum_open_interest_value": [1000, 1010, 1020, 1030, 1040, 1050, 1060],
                "count_toptrader_long_short_ratio": [1.0] * 7,
                "sum_toptrader_long_short_ratio": [1.0, 1.0, 1.0, 1.1, 1.1, 1.1, 1.2],
                "count_long_short_ratio": [1.0, 1.0, 1.0, 0.9, 0.9, 0.9, 0.8],
                "sum_taker_long_short_vol_ratio": [1.0] * 7,
            },
        )
        result = _positioning_features(frame)
        self.assertTrue(result["metrics_observed_time"].is_monotonic_increasing)
        self.assertAlmostEqual(result.iloc[3]["oi_change_15m"], 0.03)
        self.assertEqual(
            int(result.iloc[3]["metrics_observed_time_ns"]),
            int(times[3].value),
        )


if __name__ == "__main__":
    unittest.main()
