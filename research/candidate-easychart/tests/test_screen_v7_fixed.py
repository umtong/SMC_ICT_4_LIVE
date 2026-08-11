from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import unittest

import pandas as pd

from screen_v7_fixed import range_prices


class TestUnitStableSessionRanges(unittest.TestCase):
    def test_millisecond_datetime_dtype_is_compared_as_timestamp(self):
        frame = pd.DataFrame(
            {
                "open_time_dt": pd.Series(
                    pd.to_datetime(
                        [1706745600000, 1706745660000, 1706745720000],
                        unit="ms",
                        utc=True,
                    ),
                ),
                "high": [101.0, 103.0, 102.0],
                "low": [99.0, 100.0, 98.5],
            },
        )
        start_ns = pd.Timestamp("2024-02-01T00:00:00Z").value
        end_ns = pd.Timestamp("2024-02-01T00:03:00Z").value
        self.assertEqual(range_prices(frame, start_ns, end_ns), (103.0, 98.5))


if __name__ == "__main__":
    unittest.main()
