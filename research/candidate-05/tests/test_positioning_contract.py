from __future__ import annotations

import unittest

import pandas as pd

from positioning_contract import _positioning_features


class PositioningContractTest(unittest.TestCase):
    def _frame(self) -> pd.DataFrame:
        times = pd.date_range("2024-01-01", periods=7, freq="5min", tz="UTC")
        return pd.DataFrame(
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

    def test_metrics_observation_and_changes_are_causal(self) -> None:
        frame = self._frame()
        times = frame["metrics_observed_time"]
        result = _positioning_features(frame)
        self.assertTrue(result["metrics_observed_time"].is_monotonic_increasing)
        self.assertAlmostEqual(result.iloc[3]["oi_change_15m"], 0.03)
        self.assertEqual(
            int(result.iloc[3]["metrics_observed_time_ns"]),
            int(times.iloc[3].value),
        )

    def test_identical_daily_boundary_duplicate_is_collapsed(self) -> None:
        frame = self._frame()
        duplicate = frame.iloc[[3]].copy()
        combined = pd.concat([frame.iloc[:4], duplicate, frame.iloc[4:]], ignore_index=True)
        result = _positioning_features(combined)
        self.assertEqual(len(result), len(frame))
        self.assertFalse(result["metrics_observed_time"].duplicated().any())
        self.assertAlmostEqual(result.iloc[3]["oi_change_15m"], 0.03)

    def test_conflicting_duplicate_without_archive_owner_is_rejected(self) -> None:
        frame = self._frame()
        duplicate = frame.iloc[[3]].copy()
        duplicate.loc[:, "sum_open_interest"] = 999.0
        combined = pd.concat([frame, duplicate], ignore_index=True)
        with self.assertRaisesRegex(RuntimeError, "without one canonical archive owner"):
            _positioning_features(combined)

    def test_create_time_owner_wins_conflicting_midnight_overlap(self) -> None:
        before = self._frame().iloc[:3].copy()
        create_time = pd.Timestamp("2024-04-08T00:00:00Z")
        observed_time = create_time + pd.Timedelta(minutes=5)
        spillover = pd.DataFrame(
            {
                "metrics_create_time": [create_time],
                "metrics_observed_time": [observed_time],
                "_source_day": ["2024-04-07"],
                "sum_open_interest": [100.0],
                "sum_open_interest_value": [1000.0],
                "count_toptrader_long_short_ratio": [1.0],
                "sum_toptrader_long_short_ratio": [1.0],
                "count_long_short_ratio": [1.0],
                "sum_taker_long_short_vol_ratio": [1.0],
            },
        )
        owner = spillover.copy()
        owner.loc[:, "_source_day"] = "2024-04-08"
        owner.loc[:, "sum_open_interest"] = 125.0
        owner.loc[:, "sum_open_interest_value"] = 1250.0
        combined = pd.concat([before, spillover, owner], ignore_index=True, sort=False)
        result = _positioning_features(combined)
        selected = result[result["metrics_observed_time"] == observed_time]
        self.assertEqual(len(selected), 1)
        self.assertEqual(float(selected.iloc[0]["sum_open_interest"]), 125.0)
        self.assertNotIn("_source_day", result.columns)
        self.assertNotIn("metrics_create_time", result.columns)

    def test_multiple_conflicting_owner_rows_remain_invalid(self) -> None:
        create_time = pd.Timestamp("2024-04-08T00:00:00Z")
        observed_time = create_time + pd.Timedelta(minutes=5)
        rows = []
        for value in (125.0, 126.0):
            rows.append(
                {
                    "metrics_create_time": create_time,
                    "metrics_observed_time": observed_time,
                    "_source_day": "2024-04-08",
                    "sum_open_interest": value,
                    "sum_open_interest_value": value * 10.0,
                    "count_toptrader_long_short_ratio": 1.0,
                    "sum_toptrader_long_short_ratio": 1.0,
                    "count_long_short_ratio": 1.0,
                    "sum_taker_long_short_vol_ratio": 1.0,
                },
            )
        with self.assertRaisesRegex(RuntimeError, "without one canonical archive owner"):
            _positioning_features(pd.DataFrame(rows))


if __name__ == "__main__":
    unittest.main()
