from __future__ import annotations

from datetime import date
import unittest

import pandas as pd

from build_month_v3 import _premium_subset_grid


class Candidate30PremiumGapContractTest(unittest.TestCase):
    def test_missing_premium_minutes_are_allowed_but_not_invented(self) -> None:
        values = pd.Series(
            pd.to_datetime(
                [
                    "2024-08-01T00:00:00Z",
                    "2024-08-01T00:01:00Z",
                    "2024-08-01T00:03:00Z",
                ],
                utc=True,
            ),
        )
        missing = _premium_subset_grid(
            values,
            date(2024, 8, 1),
            date(2024, 8, 1),
        )
        self.assertEqual(missing, 1437)
        self.assertNotIn(
            pd.Timestamp("2024-08-01T00:02:00Z"),
            set(values),
        )

    def test_duplicate_premium_minute_is_rejected(self) -> None:
        values = pd.Series(
            pd.to_datetime(
                ["2024-08-01T00:00:00Z", "2024-08-01T00:00:00Z"],
                utc=True,
            ),
        )
        with self.assertRaisesRegex(RuntimeError, "duplicate"):
            _premium_subset_grid(
                values,
                date(2024, 8, 1),
                date(2024, 8, 1),
            )

    def test_out_of_range_premium_observation_is_rejected(self) -> None:
        values = pd.Series(
            pd.to_datetime(["2024-08-02T00:00:00Z"], utc=True),
        )
        with self.assertRaisesRegex(RuntimeError, "outside"):
            _premium_subset_grid(
                values,
                date(2024, 8, 1),
                date(2024, 8, 1),
            )


if __name__ == "__main__":
    unittest.main()
