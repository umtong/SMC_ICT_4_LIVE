from __future__ import annotations

import unittest

import pandas as pd

from cross_venue_data import assert_synchronized_completed_bars


class CrossVenueDataContractTests(unittest.TestCase):
    def test_exact_completed_timestamp_match_passes(self):
        index = pd.date_range("2024-01-01 00:01", periods=3, freq="1min", tz="UTC")
        frame = pd.DataFrame({"close": [1.0, 2.0, 3.0]}, index=index)
        assert_synchronized_completed_bars(frame, frame.copy())

    def test_one_minute_shift_fails_closed(self):
        left = pd.DataFrame(
            {"close": [1.0, 2.0]},
            index=pd.date_range("2024-01-01 00:01", periods=2, freq="1min", tz="UTC"),
        )
        right = pd.DataFrame(
            {"close": [1.0, 2.0]},
            index=pd.date_range("2024-01-01 00:02", periods=2, freq="1min", tz="UTC"),
        )
        with self.assertRaises(ValueError):
            assert_synchronized_completed_bars(left, right)


if __name__ == "__main__":
    unittest.main()
