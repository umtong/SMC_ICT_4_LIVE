from __future__ import annotations

import unittest

import pandas as pd

from timestamp_contract import datetime_values_ns
from timestamp_contract import epoch_datetime
from timestamp_contract import normalize_epoch_ns
from timestamp_contract import numeric_epoch
from timestamp_contract import timestamp_unit


class TimestampContractTest(unittest.TestCase):
    def test_string_millisecond_epochs_are_numeric_before_conversion(self) -> None:
        values = pd.Series(["1688688000000", "1688688059999"], dtype=object)
        numeric = numeric_epoch(values)
        self.assertEqual(str(numeric.dtype), "int64")
        self.assertEqual(timestamp_unit(numeric), "ms")
        converted = epoch_datetime(values)
        self.assertEqual(str(converted.iloc[0]), "2023-07-07 00:00:00+00:00")
        self.assertEqual(str(converted.iloc[1]), "2023-07-07 00:00:59.999000+00:00")

    def test_microsecond_epochs_are_supported(self) -> None:
        values = pd.Series([1688688000000000], dtype="int64")
        self.assertEqual(timestamp_unit(values), "us")
        self.assertEqual(str(epoch_datetime(values).iloc[0]), "2023-07-07 00:00:00+00:00")

    def test_microsecond_integer_serialization_normalizes_to_nanoseconds(self) -> None:
        values = pd.Series([1688688059999000, 1688688119999000], dtype="int64")
        normalized = normalize_epoch_ns(values)
        self.assertEqual(int(normalized.iloc[0]), 1688688059999000000)
        self.assertEqual(int(normalized.iloc[1]), 1688688119999000000)

    def test_datetime_values_are_explicit_nanoseconds(self) -> None:
        values = pd.Series(
            pd.to_datetime(
                [1688688059999, 1688688119999],
                unit="ms",
                utc=True,
            ),
        )
        normalized = datetime_values_ns(values)
        self.assertEqual(int(normalized.iloc[0]), 1688688059999000000)
        self.assertEqual(int(normalized.iloc[1]), 1688688119999000000)
        self.assertEqual(int(normalized.iloc[1] - normalized.iloc[0]), 60_000_000_000)


if __name__ == "__main__":
    unittest.main()
