from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import unittest
import pandas as pd
from data import _timestamp_unit


class TestData(unittest.TestCase):
    def test_binance_millisecond_and_microsecond_timestamp_units(self):
        self.assertEqual(_timestamp_unit(pd.Series([1706140800000])), "ms")
        self.assertEqual(_timestamp_unit(pd.Series([1706140800000000])), "us")


if __name__ == "__main__":
    unittest.main()
