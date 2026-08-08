from __future__ import annotations

import unittest

import pandas as pd

from features_v4b import timestamp_series_to_ns


class Candidate16V4BTimestampTests(unittest.TestCase):
    def test_microsecond_arrow_resolution_is_explicitly_scaled_to_ns(self) -> None:
        values = pd.Series(
            pd.to_datetime(
                ["2023-11-20T00:00:00Z", "2023-11-20T00:01:00Z"],
                utc=True,
            ),
        ).astype("datetime64[us, UTC]")
        raw = values.astype("int64")
        fixed = timestamp_series_to_ns(values)
        self.assertEqual(raw.iloc[0], 1_700_438_400_000_000)
        self.assertEqual(fixed.iloc[0], 1_700_438_400_000_000_000)
        self.assertEqual(fixed.iloc[1] - fixed.iloc[0], 60_000_000_000)

    def test_nanosecond_input_is_idempotent(self) -> None:
        values = pd.Series(
            pd.to_datetime(["2023-11-20T00:00:00Z"], utc=True),
        ).astype("datetime64[ns, UTC]")
        fixed = timestamp_series_to_ns(values)
        self.assertEqual(fixed.iloc[0], 1_700_438_400_000_000_000)


if __name__ == "__main__":
    unittest.main()
