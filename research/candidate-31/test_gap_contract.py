from __future__ import annotations

from datetime import date
import unittest

import pandas as pd

from build_month_gap_safe import _validate_premium_subset


class Candidate31GapContractTest(unittest.TestCase):
    def test_gap_is_counted_without_creating_a_row(self) -> None:
        values = pd.Series(pd.to_datetime([
            "2024-08-01T00:00:00Z",
            "2024-08-01T00:02:00Z",
        ], utc=True))
        self.assertEqual(
            _validate_premium_subset(values, date(2024, 8, 1), date(2024, 8, 1)),
            1438,
        )
        self.assertNotIn(pd.Timestamp("2024-08-01T00:01:00Z"), set(values))

    def test_duplicate_is_rejected(self) -> None:
        values = pd.Series(pd.to_datetime([
            "2024-08-01T00:00:00Z",
            "2024-08-01T00:00:00Z",
        ], utc=True))
        with self.assertRaisesRegex(RuntimeError, "duplicate"):
            _validate_premium_subset(values, date(2024, 8, 1), date(2024, 8, 1))


if __name__ == "__main__":
    unittest.main()
