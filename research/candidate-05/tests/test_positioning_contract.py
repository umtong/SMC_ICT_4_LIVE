from __future__ import annotations

from datetime import date
import unittest

import pandas as pd

from positioning_contract import (
    _positioning_features,
    _retain_archive_partition,
)


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

    def test_archive_partition_owns_only_matching_utc_date(self) -> None:
        raw = pd.DataFrame(
            {
                "create_time": pd.to_datetime(
                    [
                        "2024-04-07 23:55:00+00:00",
                        "2024-04-08 00:00:00+00:00",
                        "2024-04-08 23:55:00+00:00",
                        "2024-04-09 00:00:00+00:00",
                    ],
                    utc=True,
                ),
                "value": [7, 8, 9, 10],
            },
        )
        result = _retain_archive_partition(
            raw,
            partition_day=date(2024, 4, 8),
            source="BTCUSDT-metrics-2024-04-08.zip",
        )
        self.assertEqual(result["value"].tolist(), [8, 9])
        self.assertEqual(
            {stamp.date() for stamp in result["create_time"]},
            {date(2024, 4, 8)},
        )

    def test_archive_partition_rejects_empty_owned_partition(self) -> None:
        raw = pd.DataFrame(
            {
                "create_time": pd.to_datetime(
                    ["2024-04-07 23:55:00+00:00"],
                    utc=True,
                ),
            },
        )
        with self.assertRaisesRegex(RuntimeError, "contains no rows owned"):
            _retain_archive_partition(
                raw,
                partition_day=date(2024, 4, 8),
                source="BTCUSDT-metrics-2024-04-08.zip",
            )

    def test_identical_daily_boundary_duplicate_is_collapsed(self) -> None:
        frame = self._frame()
        duplicate = frame.iloc[[3]].copy()
        combined = pd.concat([frame.iloc[:4], duplicate, frame.iloc[4:]], ignore_index=True)
        result = _positioning_features(combined)
        self.assertEqual(len(result), len(frame))
        self.assertFalse(result["metrics_observed_time"].duplicated().any())
        self.assertAlmostEqual(result.iloc[3]["oi_change_15m"], 0.03)

    def test_conflicting_duplicate_inside_owned_partition_is_rejected(self) -> None:
        frame = self._frame()
        duplicate = frame.iloc[[3]].copy()
        duplicate.loc[:, "sum_open_interest"] = 999.0
        combined = pd.concat([frame, duplicate], ignore_index=True)
        with self.assertRaisesRegex(RuntimeError, "conflicting combined metrics"):
            _positioning_features(combined)


if __name__ == "__main__":
    unittest.main()
