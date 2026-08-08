from __future__ import annotations

from datetime import date
import unittest

import pandas as pd

from build_chunk import _keep_owned_metrics


class MetricsBoundaryContractTest(unittest.TestCase):
    def test_archive_keeps_only_create_time_on_filename_day(self) -> None:
        frame = pd.DataFrame(
            {
                "metrics_observed_time": pd.to_datetime(
                    [
                        "2024-05-01T00:00:00Z",  # created Apr 30 23:55
                        "2024-05-01T00:05:00Z",  # created May 1 00:00
                        "2024-05-02T00:00:00Z",  # created May 1 23:55
                        "2024-05-02T00:05:00Z",  # created May 2 00:00
                    ],
                    utc=True,
                ),
                "sum_open_interest": [1.0, 2.0, 3.0, 4.0],
            },
        )
        owned = _keep_owned_metrics(frame, date(2024, 5, 1))
        self.assertEqual(
            list(owned["sum_open_interest"]),
            [2.0, 3.0],
        )

    def test_owned_rows_must_remain_unique(self) -> None:
        frame = pd.DataFrame(
            {
                "metrics_observed_time": pd.to_datetime(
                    ["2024-05-01T00:05:00Z", "2024-05-01T00:05:00Z"],
                    utc=True,
                ),
            },
        )
        with self.assertRaisesRegex(RuntimeError, "not unique"):
            _keep_owned_metrics(frame, date(2024, 5, 1))


if __name__ == "__main__":
    unittest.main()
